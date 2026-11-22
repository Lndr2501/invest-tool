"""Sammler: Historie laden (backfill), alle 5 min aktualisieren (collect), stündlich Tagesdaten (refresh)."""
from __future__ import annotations

import datetime as dt
import logging
import time

from . import db, sources
from .config import ASSETS, settings

log = logging.getLogger("invest.collect")

FIVE_MIN = 300


def _yahoo_daily_rows(rows, meta) -> list[tuple]:
    """Yahoo-Tageskerzen → (Datum an der Börse, o, h, l, c, v). Zeitzone mit Sommerzeit (nicht gmtoffset)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    out = {}
    for r in rows:
        day = dt.datetime.fromtimestamp(r[0], tz).strftime("%Y-%m-%d")
        out[day] = (day, *r[1:])
    return list(out.values())


# ---------- Hilfsdaten ----------
def update_fx(con, full: bool = False) -> int:
    from .units import FX_CODES

    start = "1999-01-01" if full or not con.execute("SELECT 1 FROM fx_rates LIMIT 1").fetchone() else \
        (dt.date.today() - dt.timedelta(days=30)).isoformat()
    rows = sources.ecb_rates(FX_CODES, start)
    con.executemany("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)", rows)
    con.executemany("INSERT OR REPLACE INTO fx_daily VALUES (?,?)", [(d, r) for c, d, r in rows if c == "USD"])
    return len(rows)


def update_hicp(con) -> int:
    from .units import INFLATION_GEOS

    n = 0
    for geo in INFLATION_GEOS:
        rows = sources.eurostat_hicp(geo)
        con.executemany("INSERT OR REPLACE INTO hicp VALUES (?,?,?)", [(geo, m, v) for m, v in rows])
        n += len(rows)
    return n


def update_daily(con, asset: str, full: bool = False) -> int:
    m = ASSETS[asset]
    src, sym = m["daily"]
    if not full and not con.execute("SELECT 1 FROM candles_1d WHERE asset=? LIMIT 1", (asset,)).fetchone():
        full = True  # neuer Markt: komplette Historie
    adj = bool(m.get("adjusted"))
    if full:
        rows, meta = sources.yahoo_chart(sym, "1d", period1=0, period2=int(time.time()), adjusted=adj)
    else:
        # bereinigte Kurse ändern sich rückwirkend bei jeder Dividende → dann 2 Jahre neu laden
        rows, meta = sources.yahoo_chart(sym, "1d", range_="2y" if adj else "1mo", adjusted=adj)
    return db.upsert_1d(con, asset, _yahoo_daily_rows(rows, meta), meta.get("currency", "USD"), f"{src}:{sym}")


def update_daily_all(con, full: bool = False) -> str:
    ok, bad = 0, []
    for a in list(ASSETS):
        try:
            update_daily(con, a, full)
            con.commit()
            ok += 1
        except Exception as e:
            con.rollback()
            bad.append(a)
            log.warning("Tageskurse %s: %s", a, e)
        time.sleep(0.15)
    if bad and not ok:
        raise sources.SourceError(f"alle fehlgeschlagen: {bad}")
    return f"{ok} Märkte" + (f", fehlgeschlagen: {', '.join(bad)}" if bad else "")


def update_spot(con) -> int:
    n = 0
    for asset, a in ASSETS.items():
        if a.get("spot"):
            try:
                ts, price = sources.goldapi_price(a["spot"], "EUR")
            except Exception as e:
                log.warning("Spot %s: %s", asset, e)
                continue
            con.execute("INSERT OR REPLACE INTO spot VALUES (?,?,?,?)", (asset, ts, price, f"gold-api:{a['spot']}"))
            n += 1
    return n


# ---------- 5-min-Kerzen ----------
NEW_HISTORY_DAYS = 60  # neue Märkte: 5-min-Kerzen nur der letzten 60 Tage (Tageskurse komplett)
FULL_5M = ("BTC", "GOLD")  # nur hier die komplette 5-min-Historie (Backtest Minuten-Muster)


def _intraday_one(con, asset: str, src: str, sym: str, cur: str, full: bool) -> int:
    last = db.last_ts_5m(con, asset)
    now = int(time.time())
    if src == "binance":
        if last is None:
            start = 0 if (full and asset in FULL_5M) else now - NEW_HISTORY_DAYS * 86400
        else:
            start = last - 2 * FIVE_MIN
        if now - start > 3 * 86400:
            n = [0]

            def batch(rows):
                n[0] += db.upsert_5m(con, asset, rows, cur, f"binance:{sym}")
                con.commit()

            sources.binance_history(sym, start, batch)
            return n[0]
        return db.upsert_5m(con, asset, sources.binance_klines(sym, "5m", start_ms=start * 1000), cur, f"binance:{sym}")
    if src == "kraken":
        return db.upsert_5m(con, asset, sources.kraken_ohlc(sym, 5), cur, f"kraken:{sym}")
    if src == "yahoo":
        gap = now - (last or 0)
        rng = "1d" if gap < 20 * 3600 else "5d" if gap < 4 * 86400 else "60d"
        if full:
            rng = "60d"
        rows, meta = sources.yahoo_chart(sym, "5m", range_=rng)
        return db.upsert_5m(con, asset, rows, meta.get("currency", cur), f"yahoo:{sym}")
    raise ValueError(src)


def update_intraday(con, asset: str, full: bool = False) -> tuple[int, str]:
    errors = []
    for src, sym, cur in ASSETS[asset]["intraday"]:
        try:
            return _intraday_one(con, asset, src, sym, cur, full), f"{src}:{sym}"
        except Exception as e:  # nächste Quelle versuchen
            errors.append(f"{src}:{sym}: {e}")
            log.warning("%s: Quelle %s fehlgeschlagen: %s", asset, src, e)
    raise sources.SourceError("; ".join(errors))


def update_intraday_all(con, full: bool = False) -> str:
    """Alle Märkte: Börsen-APIs einzeln, Yahoo gebündelt (ein Abruf für bis zu 20 Kürzel)."""
    now = int(time.time())
    yahoo: dict[str, list[tuple[str, str, str]]] = {"1d": [], "5d": []}
    single: list[str] = []  # Yahoo-Märkte ohne aktuelle Daten → einzeln mit langer Historie
    ok, bad = 0, []
    for a, m in list(ASSETS.items()):
        srcs = list(m["intraday"])
        done = False
        for i, (src, sym, cur) in enumerate(srcs):
            if src == "yahoo":
                last = db.last_ts_5m(con, a)
                gap = now - (last or 0)
                if full or last is None or gap > 4 * 86400:
                    single.append(a)
                else:
                    yahoo["1d" if gap < 20 * 3600 else "5d"].append((a, sym, cur))
                done = True
                break
            try:
                _intraday_one(con, a, src, sym, cur, full)
                con.commit()
                ok += 1
                done = True
                break
            except Exception as e:
                con.rollback()
                log.warning("%s: Quelle %s fehlgeschlagen: %s", a, src, e)
        if not done:
            bad.append(a)
    for rng, items in yahoo.items():
        if not items:
            continue
        try:
            got = sources.yahoo_spark([sym for _, sym, _ in items], range_=rng)
        except Exception as e:
            log.warning("Yahoo-Sammelabruf %s: %s", rng, e)
            got = {}
        for a, sym, cur in items:
            if sym in got and got[sym][0]:
                rows, meta = got[sym]
                db.upsert_5m(con, a, rows, meta.get("currency") or cur, f"yahoo:{sym}")
                ok += 1
            else:
                single.append(a)
        con.commit()
    for a in single:
        try:
            src, sym, cur = next(x for x in ASSETS[a]["intraday"] if x[0] == "yahoo")
            _intraday_one(con, a, src, sym, cur, full=True if db.last_ts_5m(con, a) is None else full)
            con.commit()
            ok += 1
        except Exception as e:
            con.rollback()
            bad.append(a)
            log.warning("%s 5 min: %s", a, e)
        time.sleep(0.2)
    if bad and not ok:
        raise sources.SourceError(f"alle fehlgeschlagen: {bad}")
    return f"{ok} Märkte" + (f", ohne Daten: {', '.join(sorted(set(bad)))}" if bad else "")


def init_market(con, asset: str) -> None:
    """Neu hinzugefügter Markt: Tageshistorie + 5-min-Kerzen der letzten Wochen sofort laden."""
    update_daily(con, asset, full=True)
    con.commit()
    try:
        update_intraday(con, asset)
        con.commit()
    except Exception as e:
        log.warning("%s 5 min: %s", asset, e)


# ---------- Jobs ----------
def _job(name: str, steps: list[tuple[str, callable]]) -> list[str]:
    con = db.connect()
    msgs, ok = [], True
    for label, fn in steps:
        t0 = time.time()
        try:
            res = fn(con)
            con.commit()
            msgs.append(f"{label}: {res} ({time.time() - t0:.1f}s)")
        except Exception as e:
            ok = False
            con.rollback()
            msgs.append(f"{label}: FEHLER {e}")
            log.exception("%s fehlgeschlagen", label)
    db.log_run(con, name, ok, " | ".join(msgs))
    con.commit()
    con.close()
    return msgs


def backfill() -> list[str]:
    steps = [("Wechselkurse", lambda c: update_fx(c, full=True)), ("Inflation", update_hicp),
             ("Tageskurse", lambda c: update_daily_all(c, full=True)),
             ("5 min", lambda c: update_intraday_all(c, full=True)), ("Spot", update_spot)]
    return _job("backfill", steps)


def collect() -> list[str]:
    steps = [("5 min", update_intraday_all), ("Spot", update_spot)]
    msgs = _job("collect", steps)
    from . import alerts  # nach dem Speichern prüfen

    msgs += alerts.check()
    return msgs


def refresh() -> list[str]:
    steps = [("Wechselkurse", update_fx), ("Inflation", update_hicp), ("Tageskurse", update_daily_all)]
    msgs = _job("refresh", steps)
    from . import analysis

    msgs += analysis.run_all()
    return msgs
