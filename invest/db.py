"""SQLite-Speicher (WAL: Sammler schreibt, Oberfläche liest gleichzeitig).

Preise werden in Originalwährung gespeichert und beim Laden mit dem EZB-Kurs in EUR umgerechnet.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Iterable

import pandas as pd

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles_5m(
  asset TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
  cur TEXT, src TEXT, PRIMARY KEY(asset, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS candles_1d(
  asset TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  cur TEXT, src TEXT, PRIMARY KEY(asset, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS spot(asset TEXT, ts INTEGER, price_eur REAL, src TEXT, PRIMARY KEY(asset, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fx_daily(day TEXT PRIMARY KEY, eurusd REAL);
CREATE TABLE IF NOT EXISTS hicp(geo TEXT, month TEXT, idx REAL, PRIMARY KEY(geo, month));
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, ts INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, asset TEXT,
  rule TEXT, direction TEXT, message TEXT, sent INTEGER);
CREATE INDEX IF NOT EXISTS alerts_lookup ON alerts(asset, rule, direction, ts);
CREATE TABLE IF NOT EXISTS runs(ts INTEGER, job TEXT, ok INTEGER, msg TEXT);
-- v2: Wechselkurse (1 EUR = rate CUR), Profile, Bestand, Sparpläne, Telegram je Profil
CREATE TABLE IF NOT EXISTS fx_rates(cur TEXT, day TEXT, rate REAL, PRIMARY KEY(cur, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE COLLATE NOCASE,
  pin_hash TEXT, settings TEXT, tg_chat_id TEXT, created INTEGER);
CREATE TABLE IF NOT EXISTS holdings(id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL,
  asset TEXT, form TEXT, label TEXT, count REAL, weight REAL, unit TEXT, fineness REAL, coin TEXT,
  quantity REAL, buy_date TEXT, buy_price REAL, buy_cur TEXT, note TEXT, created INTEGER);
CREATE INDEX IF NOT EXISTS holdings_profile ON holdings(profile_id);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL,
  asset TEXT, amount REAL, cur TEXT, start TEXT, end TEXT, created INTEGER);
CREATE INDEX IF NOT EXISTS plans_profile ON plans(profile_id);
CREATE TABLE IF NOT EXISTS tg_links(code TEXT PRIMARY KEY, profile_id INTEGER, created INTEGER);
CREATE TABLE IF NOT EXISTS tg_log(id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER, ts INTEGER,
  kind TEXT, key TEXT, text TEXT, sent INTEGER);
CREATE INDEX IF NOT EXISTS tg_log_lookup ON tg_log(profile_id, kind, ts);
-- v3: Nutzer-Märkte, geheime Werte (API-Keys), KI-Verlauf
CREATE TABLE IF NOT EXISTS markets(id TEXT PRIMARY KEY, def TEXT, added_by INTEGER, created INTEGER);
CREATE TABLE IF NOT EXISTS secrets(scope TEXT, name TEXT, value TEXT, PRIMARY KEY(scope, name));
CREATE TABLE IF NOT EXISTS ai_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER, ts INTEGER,
  role TEXT, content TEXT);
CREATE INDEX IF NOT EXISTS ai_messages_profile ON ai_messages(profile_id, id);
"""


def connect(readonly: bool = False) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)  # je Anfrage eigene Verbindung
    con.execute("PRAGMA busy_timeout=60000")
    if not readonly:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(SCHEMA)
    else:
        con.execute("PRAGMA query_only=1")
    from . import markets

    markets.sync(con)
    return con


# ---------- Schreiben ----------
def upsert_5m(con, asset: str, rows: Iterable[tuple], cur: str, src: str) -> int:
    data = [(asset, r[0], r[1], r[2], r[3], r[4], r[5], cur, src) for r in rows]
    con.executemany("INSERT OR REPLACE INTO candles_5m VALUES (?,?,?,?,?,?,?,?,?)", data)
    return len(data)


def upsert_1d(con, asset: str, rows: Iterable[tuple], cur: str, src: str) -> int:
    """rows: (tag 'YYYY-MM-DD', o, h, l, c, v)"""
    data = [(asset, r[0], r[1], r[2], r[3], r[4], r[5], cur, src) for r in rows]
    con.executemany("INSERT OR REPLACE INTO candles_1d VALUES (?,?,?,?,?,?,?,?,?)", data)
    return len(data)


def kv_set(con, key: str, data) -> None:
    con.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, int(time.time()), json.dumps(data, default=str)))


def kv_get(con, key: str, default=None):
    row = con.execute("SELECT data FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_time(con, key: str) -> int | None:
    row = con.execute("SELECT ts FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def log_run(con, job: str, ok: bool, msg: str = "") -> None:
    con.execute("INSERT INTO runs VALUES (?,?,?,?)", (int(time.time()), job, int(ok), msg[:2000]))
    con.execute("DELETE FROM runs WHERE ts < ?", (int(time.time()) - 30 * 86400,))


def last_ts_5m(con, asset: str) -> int | None:
    row = con.execute("SELECT MAX(ts) FROM candles_5m WHERE asset=?", (asset,)).fetchone()
    return row[0]


# ---------- Lesen (alles in EUR) ----------
def fx_frame(con) -> pd.DataFrame:
    df = pd.read_sql("SELECT day, eurusd FROM fx_daily ORDER BY day", con)
    df["day"] = pd.to_datetime(df["day"])
    return df


_FX_CACHE: dict = {}


def _fx_table(con, cur: str) -> pd.DataFrame:
    """1 EUR = rate CUR (EZB), kurz zwischengespeichert. Leer, wenn unbekannt."""
    hit = _FX_CACHE.get(cur)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    df = pd.read_sql("SELECT day AS _fxday, rate FROM fx_rates WHERE cur=? ORDER BY day", con, params=(cur,))
    if df.empty and cur == "USD":
        df = pd.read_sql("SELECT day AS _fxday, eurusd AS rate FROM fx_daily ORDER BY day", con)
    df["_fxday"] = pd.to_datetime(df["_fxday"])
    _FX_CACHE[cur] = (time.time(), df)
    return df


# Yahoo meldet Londoner Kurse teils in Pence
_SUBUNIT = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def _to_eur(df: pd.DataFrame, con, time_col: str) -> pd.DataFrame:
    """Kurse jeder Währung mit dem EZB-Kurs des jeweiligen Tages in EUR umrechnen."""
    if df.empty or (df["cur"] == "EUR").all():
        return df
    df = df.sort_values(time_col).reset_index(drop=True)
    for cur in [c for c in df["cur"].dropna().unique() if c != "EUR"]:
        base, div = _SUBUNIT.get(cur, (cur, 1.0))
        mask = df["cur"] == cur
        part = df.loc[mask, [time_col]].copy()
        if base == "EUR":
            rate = pd.Series(1.0, index=part.index)
        else:
            fx = _fx_table(con, base)
            if fx.empty:
                raise RuntimeError(f"Keine Wechselkurse für {base} – erst 'refresh' ausführen.")
            m = pd.merge_asof(part, fx, left_on=time_col, right_on="_fxday", direction="backward")
            rate = pd.Series(m["rate"].bfill().values, index=part.index)
        for c in ("open", "high", "low", "close"):
            df.loc[mask, c] = df.loc[mask, c] / div / rate
    return df


def load_daily(con, asset: str) -> pd.DataFrame:
    df = pd.read_sql("SELECT day, open, high, low, close, volume, cur FROM candles_1d WHERE asset=? ORDER BY day",
                     con, params=(asset,))
    df["day"] = pd.to_datetime(df["day"])
    df = _to_eur(df, con, "day")
    return df.set_index("day")[["open", "high", "low", "close", "volume"]]


def load_5m(con, asset: str, since: int | None = None) -> pd.DataFrame:
    q = "SELECT ts, open, high, low, close, volume, cur, src FROM candles_5m WHERE asset=?"
    params: list = [asset]
    if since is not None:
        q += " AND ts>=?"
        params.append(int(since))
    df = pd.read_sql(q + " ORDER BY ts", con, params=params)
    df["dt"] = pd.to_datetime(df["ts"], unit="s")
    df = _to_eur(df, con, "dt")
    return df.set_index("dt")[["ts", "open", "high", "low", "close", "volume", "src"]]


def load_spot(con, asset: str, since: int | None = None) -> pd.DataFrame:
    df = pd.read_sql("SELECT ts, price_eur FROM spot WHERE asset=? AND ts>=? ORDER BY ts", con,
                     params=(asset, int(since or 0)))
    df["dt"] = pd.to_datetime(df["ts"], unit="s")
    return df.set_index("dt")


def load_hicp(con, geo: str) -> pd.Series:
    df = pd.read_sql("SELECT month, idx FROM hicp WHERE geo=? ORDER BY month", con, params=(geo,))
    return pd.Series(df["idx"].values, index=pd.PeriodIndex(df["month"], freq="M"), name="hicp")


def fx_rates(con, cur: str) -> pd.Series:
    """1 EUR = x CUR, täglich (EZB). EUR → konstante 1."""
    if cur == "EUR":
        return pd.Series(dtype=float)
    df = pd.read_sql("SELECT day, rate FROM fx_rates WHERE cur=? ORDER BY day", con, params=(cur,))
    if df.empty and cur == "USD":
        df = pd.read_sql("SELECT day, eurusd AS rate FROM fx_daily ORDER BY day", con)
    return pd.Series(df["rate"].values, index=pd.to_datetime(df["day"]), name=cur)


def fx_now(con, cur: str) -> float:
    if cur == "EUR":
        return 1.0
    s = fx_rates(con, cur)
    if s.empty:
        raise RuntimeError(f"Kein Wechselkurs für {cur} – erst 'refresh' ausführen.")
    return float(s.iloc[-1])
