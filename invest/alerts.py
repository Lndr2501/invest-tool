"""Alarme und Berichte: prüft nach jedem 5-min-Lauf und verteilt an die Profile.

1. Ereignisse erkennen (einmal für alle): Bewegung in 1 h / 24 h, ungewöhnliche Stunde, Allzeithoch,
   Trendwechsel, Schwankungswechsel. Zwei Stufen: „normal“ (Schwelle × 1) und „stark“ (× 1,5).
   Sperrzeit je Asset/Regel/Richtung/Stufe verhindert Wiederholungen.
2. Verteilen: jedes Profil bekommt nur, was es eingestellt hat (Stufe, Regeln, Anlagen, Tageslimit),
   in seiner Währung und Einheit.
3. Berichte nach Zeitplan des Profils (täglich / wöchentlich / monatlich, Stunde wählbar).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import math
import time
from zoneinfo import ZoneInfo

import pandas as pd

from . import db, markets, profiles, service, telegram
from .config import ASSETS, CONF_DIR, settings, telegram_conf
from .units import fmt_money, fmt_num

log = logging.getLogger("invest.alerts")
TZ = ZoneInfo("Europe/Berlin")
LEVELS = {"normal": 1.0, "strong": 1.5}
GROUP = {"move_1h": "moves", "zscore_1h": "moves", "move_24h": "moves", "ath": "ath", "trend": "trend",
         "regime": "regime"}
# Beruhigender Schluss für Alarme: sagt, was das heißt, ohne zu beraten
CALM_DOWN = ("<i>Nur ein Hinweis, keine Aufforderung zum Verkaufen. Solche Stürze gab es schon oft. Wichtig ist vor "
             "allem, wann du das Geld brauchst.</i>")
CALM_UP = "<i>Nur ein Hinweis, keine Aufforderung zum Kaufen. Kurse können auch schnell wieder fallen.</i>"
CALM = "<i>Nur ein Hinweis, keine Aufforderung zum Kaufen oder Verkaufen.</i>"


# ---------- CLI-Hilfen (wie v1) ----------
def telegram_setup() -> list[str]:
    tok = telegram_conf().get("TELEGRAM_TOKEN")
    if not tok:
        return [f"FEHLER kein TELEGRAM_TOKEN in {CONF_DIR / 'telegram.env'}"]
    name = telegram.bot_username()
    return [f"Bot @{name} bereit. Verknüpfen pro Profil auf der Website unter Einstellungen → Telegram verbinden."]


def telegram_test() -> list[str]:
    con = db.connect()
    out = []
    for p in profiles.list_profiles(con):
        if p["tg_chat_id"]:
            ok = telegram.send(p["tg_chat_id"], f"✅ Der <b>Invest-Ratgeber</b> ist mit dem Profil "
                                                f"{html.escape(p['name'])} verbunden. Hier kommen deine Berichte "
                                                "und Alarme an.")
            out.append(f"{p['name']}: {'gesendet' if ok else 'FEHLER'}")
    con.close()
    return out or ["Kein Profil mit Telegram verbunden."]


# ---------- Erkennen ----------
def tracked_assets(con) -> list[str]:
    """Nur Märkte prüfen, die jemand beobachtet oder besitzt (plus Kernmärkte)."""
    want = set(markets.CORE)
    for p in profiles.list_profiles(con):
        want |= set(p["settings"]["assets"])
    for (a,) in con.execute("SELECT asset FROM holdings UNION SELECT asset FROM plans"):
        want.add(a)
    return [a for a in ASSETS if a in want]


def profile_assets(con, p: dict) -> set[str]:
    rows = con.execute("SELECT asset FROM holdings WHERE profile_id=? UNION SELECT asset FROM plans WHERE profile_id=?",
                       (p["id"], p["id"])).fetchall()
    return set(p["settings"]["assets"]) | {r[0] for r in rows}


def thresholds(a: str, ana: dict, conf: dict) -> dict:
    """Schwellen in %: fest aus settings.toml, sonst aus der üblichen Schwankung (3 × typischer Tag)."""
    if a in conf:
        return conf[a]
    v = ana.get("vol") or {}
    vol, ppy = v.get("median_vol") or v.get("vol30"), v.get("periods_per_year") or 252
    if not vol:
        return {}
    day = vol / math.sqrt(ppy) * 3 * 100
    m24 = round(min(max(day, 2.0), 20.0) * 2) / 2
    return {"move_1h": m24 / 2, "move_24h": m24}


def _cooldown_ok(con, asset: str, rule: str, direction: str, hours: float, now: float) -> bool:
    row = con.execute("SELECT MAX(ts) FROM alerts WHERE asset=? AND rule=? AND direction=?",
                      (asset, rule, direction)).fetchone()
    return row[0] is None or now - row[0] >= hours * 3600


def detect(con, now: float) -> list[dict]:
    s = settings()["alerts"]
    cd = float(s.get("cooldown_hours", 3))
    events = []

    def fire(asset, rule, level, direction, data, hours):
        key = f"{rule}:{level}"
        if _cooldown_ok(con, asset, key, direction, hours, now):
            ev = {"asset": asset, "rule": rule, "level": level, "direction": direction, **data}
            con.execute("INSERT INTO alerts(ts, asset, rule, direction, message, sent) VALUES (?,?,?,?,?,?)",
                        (int(now), asset, key, direction, _neutral_text(ev), 0))
            events.append(ev)

    for a in tracked_assets(con):
        try:
            df = db.load_5m(con, a, since=int(now - 3 * 86400))
            if df.empty or now - df["ts"].iloc[-1] > 20 * 60:
                continue  # Markt geschlossen oder Daten veraltet
            ana = db.kv_get(con, f"analysis:{a}") or {}
            c = df["close"]
            p = float(c.iloc[-1])
            p1 = c.asof(c.index[-1] - pd.Timedelta(hours=1))
            p24 = c.asof(c.index[-1] - pd.Timedelta(hours=24))
            th = thresholds(a, ana, s)
            if p1 and p1 == p1:
                r1 = p / p1 - 1
                d = "up" if r1 > 0 else "down"
                hit = False
                for lvl, f in LEVELS.items():
                    if abs(r1) * 100 >= th.get("move_1h", 99) * f:
                        hit = True
                        fire(a, "move_1h", lvl, d, {"r": r1, "p": p}, cd)
                sd = (ana.get("intraday") or {}).get("std1h")
                if sd and not hit:
                    z = math.log(p / p1) / sd
                    if abs(z) >= s.get("zscore_1h", 4):
                        fire(a, "zscore_1h", "normal", d, {"r": r1, "p": p, "z": abs(z)}, cd)
            if p24 and p24 == p24:
                r24 = p / p24 - 1
                d = "up" if r24 > 0 else "down"
                for lvl, f in LEVELS.items():
                    if abs(r24) * 100 >= th.get("move_24h", 99) * f:
                        fire(a, "move_24h", lvl, d, {"r": r24, "p": p}, cd * 2)
            ref = (ana.get("ath_ref") or {}).get("value")
            if ref and p > ref:
                fire(a, "ath", "strong", "up", {"p": p, "ref": ref}, 72)
            tc = ana.get("trend_change")
            if tc and now - tc["ts"] < 2 * 3600:
                strong = ("aufwärts" in tc["to"]) != ("aufwärts" in tc["from"]) and "seitwärts" not in tc["to"]
                fire(a, "trend", "strong" if strong else "normal", tc["to"], {"from": tc["from"], "to": tc["to"], "p": p}, 24)
            rc = ana.get("regime_change")
            if rc and now - rc["ts"] < 2 * 3600:
                fire(a, "regime", "normal", rc["to"], {"from": rc["from"], "to": rc["to"], "p": p}, 24)
        except Exception:
            log.exception("Alarmprüfung %s", a)
    con.commit()
    return events


def _neutral_text(ev: dict) -> str:
    name = ASSETS[ev["asset"]]["name"]
    r = ev.get("r")
    return {
        "move_1h": f"{name} {r * 100:+.1f} % in 1 Stunde",
        "zscore_1h": f"{name}: ungewöhnliche Stunde ({r * 100:+.1f} %)" if r is not None else name,
        "move_24h": f"{name} {r * 100:+.1f} % in 24 Stunden",
        "ath": f"{name}: neues Allzeithoch",
        "trend": f"{name}: Trend {ev.get('from')} → {ev.get('to')}",
        "regime": f"{name}: Schwankung {ev.get('from')} → {ev.get('to')}",
    }[ev["rule"]] + f" [{ev['level']}]"


# ---------- Verteilen ----------
def _chg(r: float) -> str:
    return f"{'+' if r >= 0 else '−'}{abs(r) * 100:.1f} %".replace(".", ",")


def format_event(con, profile: dict, ev: dict) -> str:
    a = ev["asset"]
    name = ASSETS[a]["name"]
    pdp = service.price_display(con, profile, a)
    price = f"Jetzt {pdp['text']} für {pdp['label'].replace('1 Gramm', '1 g').replace('1 Unze', '1 oz').replace('1 Kilo', '1 kg')}"
    rule = ev["rule"]
    if rule in ("move_1h", "move_24h", "zscore_1h"):
        r = ev["r"]
        span = "1 Stunde" if rule != "move_24h" else "24 Stunden"
        icon = ("🔺" if r > 0 else "🔻") if rule != "move_24h" else ("📈" if r > 0 else "📉")
        head = f"{icon} <b>{name} {_chg(r)} in {span}</b>\nDer Kurs ist deutlich {'gestiegen' if r > 0 else 'gefallen'}."
        if rule == "zscore_1h":
            head = (f"⚠️ <b>{name}: ungewöhnliche Stunde ({_chg(r)})</b>\nDer Kurs hat sich {fmt_num(ev['z'], 1)}× so stark "
                    "bewegt wie in einer üblichen Stunde.")
        body = [head, price]
        pf = service.portfolio(con, profile)
        mine = next((x for x in pf["alloc"] if x["asset"] == a), None)
        if mine:
            delta = mine["value"] * (1 - 1 / (1 + r))
            body.append(f"Dein {name}: {'+' if delta >= 0 else '−'}{fmt_money(abs(delta), pf['currency'])} in {span}")
        body.append(CALM_UP if r > 0 else CALM_DOWN)
        return "\n".join(body)
    if rule == "ath":
        return (f"🏆 <b>{name}: neues Allzeithoch</b>\nDer Kurs ist so hoch wie noch nie (höchster Stand bisher).\n"
                f"{price}\n{CALM}")
    if rule == "trend":
        return (f"🧭 <b>{name}: Trend dreht</b>\nDer Trend ist die Richtung, in die der Kurs in den letzten Wochen "
                f"und Monaten lief.\n{ev['from']} → <b>{ev['to']}</b>\n{price}\n{CALM}")
    return (f"🔄 <b>{name}: Schwankung ändert sich</b>\nSchwankung heißt: wie stark der Kurs hoch und runter geht.\n"
            f"{ev['from']} → <b>{ev['to']}</b>\n{price}\n{CALM}")


def _today_start() -> int:
    now = dt.datetime.now(TZ)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def dispatch(con, events: list[dict]) -> list[str]:
    out = []
    if not events:
        return out
    for p in profiles.list_profiles(con):
        al = p["settings"]["alerts"]
        if not p["tg_chat_id"] or al["level"] == "off":
            continue
        allowed = {"strong"} if al["level"] == "strong" else {"strong", "normal"}
        mine = profile_assets(con, p)
        cand = [e for e in events if e["level"] in allowed and GROUP[e["rule"]] in al["rules"]
                and e["asset"] in mine]
        # pro Asset/Regel nur die stärkste Stufe
        best: dict = {}
        for e in cand:
            k = (e["asset"], e["rule"], e["direction"])
            if k not in best or e["level"] == "strong":
                best[k] = e
        sent_today = con.execute("SELECT COUNT(*) FROM tg_log WHERE profile_id=? AND kind='alert' AND ts>=? AND sent=1",
                                 (p["id"], _today_start())).fetchone()[0]
        for e in best.values():
            if sent_today >= al["max_per_day"]:
                out.append(f"{p['name']}: Tageslimit erreicht")
                break
            text = format_event(con, p, e)
            ok = telegram.send(p["tg_chat_id"], text)
            telegram.log_send(con, p["id"], "alert", f"{e['asset']}:{e['rule']}:{e['level']}:{e['direction']}", text, ok)
            sent_today += int(ok)
            out.append(f"Alarm an {p['name']}: {e['asset']} {e['rule']} ({'gesendet' if ok else 'FEHLER'})")
    return out


def report_due(p: dict, local: dt.datetime) -> bool:
    r = p["settings"]["report"]
    if r["freq"] == "off" or local.hour != r["hour"]:
        return False
    if r["freq"] == "weekly" and local.weekday() != r["weekday"]:
        return False
    if r["freq"] == "monthly" and local.day != 1:
        return False
    return True


def reports(con, force: bool = False) -> list[str]:
    out = []
    local = dt.datetime.now(TZ)
    day = local.date().isoformat()
    for p in profiles.list_profiles(con):
        if not p["tg_chat_id"] or not (force or report_due(p, local)):
            continue
        if not force and con.execute("SELECT 1 FROM tg_log WHERE profile_id=? AND kind='report' AND key=? AND sent=1",
                                     (p["id"], day)).fetchone():
            continue
        text = telegram.report_text(con, p)
        ok = telegram.send(p["tg_chat_id"], text)
        telegram.log_send(con, p["id"], "report", day, text, ok)
        out.append(f"Bericht an {p['name']}: {'gesendet' if ok else 'FEHLER'}")
    return out


def check() -> list[str]:
    now = time.time()
    con = db.connect()
    profiles.migrate_v1(con)
    service.clear_cache()
    out = []
    try:
        events = detect(con, now)
        out += [f"Ereignis {e['asset']} {e['rule']} {e['level']} {e['direction']}" for e in events]
        for label, fn in (("Alarme", lambda: dispatch(con, events)), ("Berichte", lambda: reports(con))):
            try:
                out += fn()
            except Exception as e:
                log.exception("%s fehlgeschlagen", label)
                out.append(f"{label}: FEHLER {type(e).__name__}: {e}")
    finally:
        con.commit()
        con.close()
    return out


def daily_report(force: bool = True, con=None) -> list[str]:
    """CLI `report`: Bericht sofort an alle verbundenen Profile."""
    own = con is None
    con = con or db.connect()
    out = reports(con, force=force)
    if own:
        con.close()
    return out or ["Kein Profil mit Telegram verbunden."]
