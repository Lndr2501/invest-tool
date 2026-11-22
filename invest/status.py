"""Datenstand auf einen Blick."""
from __future__ import annotations

import datetime as dt
import time

from . import db
from .config import ASSETS


def _fmt(ts):
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d %H:%M UTC") if ts else "–"


def status() -> list[str]:
    con = db.connect(readonly=True)
    out = []
    now = time.time()
    for a in ASSETS:
        n5, t0, t1 = con.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM candles_5m WHERE asset=?", (a,)).fetchone()
        nd, d0, d1 = con.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM candles_1d WHERE asset=?", (a,)).fetchone()
        age = f"{(now - t1) / 60:.0f} min alt" if t1 else ""
        out.append(f"{a:7} alle 5 min: {n5:>8} Kurse {_fmt(t0)} → {_fmt(t1)} ({age}) | "
                   f"täglich: {nd:>5} Tage {d0} → {d1}")
    fx = con.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM fx_daily").fetchone()
    hi = con.execute("SELECT geo, COUNT(*), MIN(month), MAX(month) FROM hicp GROUP BY geo").fetchall()
    out.append(f"Wechselkurs EUR/USD: {fx[0]} Tage {fx[1]} → {fx[2]} | Inflation (HVPI): {hi}")
    for job, ts, ok, msg in con.execute(
        "SELECT job, MAX(ts), ok, msg FROM runs GROUP BY job ORDER BY job").fetchall():
        out.append(f"letzter Lauf {job}: {_fmt(ts)} {'OK' if ok else 'FEHLER'} – {msg[:300]}")
    return out
