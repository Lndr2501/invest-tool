"""Datenquellen. Alle kostenlos und ohne API-Key (getestet 22.11.2026).

Jede Kerzen-Funktion liefert Tupel (unix_sekunden, open, high, low, close, volume).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import time
from typing import Callable

import requests

_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) invest-tool/1.0"})

Row = tuple[int, float, float, float, float, float]


class SourceError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None, timeout: int = 20, tries: int = 3, as_json: bool = True):
    last: Exception | None = None
    for i in range(tries):
        try:
            r = _s.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = SourceError(f"{url} HTTP {r.status_code}")
            time.sleep(3 * (i + 1))
            continue
        if r.status_code != 200:
            raise SourceError(f"{url} HTTP {r.status_code}: {r.text[:200]}")
        return r.json() if as_json else r.text
    raise SourceError(f"{url}: {last}")


# ---------- Binance (Krypto + PAXG, volle 5-min-Historie) ----------
def binance_klines(symbol: str, interval: str = "5m", start_ms: int | None = None, limit: int = 1000) -> list[Row]:
    p = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        p["startTime"] = int(start_ms)
    data = _get("https://api.binance.com/api/v3/klines", p)
    return [(int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in data]


def binance_history(symbol: str, start_s: int, on_batch: Callable[[list[Row]], None],
                    interval: str = "5m", step: int = 300, pause: float = 0.12) -> int:
    """Holt alle Kerzen ab start_s bis jetzt, 1000 pro Abruf."""
    cur, total = start_s, 0
    while True:
        rows = binance_klines(symbol, interval, start_ms=cur * 1000)
        if not rows:
            break
        on_batch(rows)
        total += len(rows)
        nxt = rows[-1][0] + step
        if len(rows) < 1000 or nxt <= cur:
            break
        cur = nxt
        time.sleep(pause)
    return total


# ---------- Kraken (Ersatz, letzte 720 Kerzen) ----------
def kraken_ohlc(pair: str, interval: int = 5) -> list[Row]:
    j = _get("https://api.kraken.com/0/public/OHLC", {"pair": pair, "interval": interval})
    if j.get("error"):
        raise SourceError(f"Kraken {pair}: {j['error']}")
    key = next(k for k in j["result"] if k != "last")
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])) for r in j["result"][key]]


# ---------- Yahoo Finance (inoffiziell; 5 min nur 60 Tage zurück) ----------
def yahoo_chart(symbol: str, interval: str = "5m", range_: str | None = None,
                period1: int | None = None, period2: int | None = None,
                adjusted: bool = False) -> tuple[list[Row], dict]:
    """adjusted=True: Dividenden/Splits eingerechnet (bereinigter Schlusskurs, nur Tageskerzen)."""
    p: dict = {"interval": interval, "includePrePost": "false"}
    if range_:
        p["range"] = range_
    else:
        p["period1"] = int(period1 or 0)
        p["period2"] = int(period2 or time.time())
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", p, timeout=30)
    res = (j.get("chart") or {}).get("result")
    if not res:
        raise SourceError(f"Yahoo {symbol}: {j.get('chart', {}).get('error')}")
    r = res[0]
    ts = r.get("timestamp") or []
    q = r["indicators"]["quote"][0]
    adj = ((r["indicators"].get("adjclose") or [{}])[0].get("adjclose")) if adjusted else None
    rows: list[Row] = []
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        o, h, lo, v = q["open"][i], q["high"][i], q["low"][i], q["volume"][i]
        f = (adj[i] / c) if adj and adj[i] is not None and c else 1.0
        rows.append((int(t), float(o if o is not None else c) * f, float(h if h is not None else c) * f,
                     float(lo if lo is not None else c) * f, float(c) * f, float(v or 0)))
    return rows, r.get("meta", {})


def yahoo_spark(symbols: list[str], range_: str = "1d", interval: str = "5m") -> dict[str, tuple[list[Row], dict]]:
    """Viele Kürzel in einem Abruf (nur Schlusskurse). Fehlende Kürzel fehlen im Ergebnis."""
    out: dict[str, tuple[list[Row], dict]] = {}
    for i in range(0, len(symbols), 20):
        chunk = symbols[i:i + 20]
        j = _get("https://query1.finance.yahoo.com/v7/finance/spark",
                 {"symbols": ",".join(chunk), "range": range_, "interval": interval}, timeout=30)
        for res in ((j.get("spark") or {}).get("result") or []):
            try:
                resp = res["response"][0]
                ts = resp.get("timestamp") or []
                closes = resp["indicators"]["quote"][0]["close"]
                rows = [(int(t), float(c), float(c), float(c), float(c), 0.0) for t, c in zip(ts, closes) if c is not None]
                out[res["symbol"]] = (rows, resp.get("meta", {}))
            except (KeyError, IndexError, TypeError):
                continue
    return out


def yahoo_search(query: str, limit: int = 10) -> list[dict]:
    j = _get("https://query1.finance.yahoo.com/v1/finance/search",
             {"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0}, timeout=15, tries=2)
    return j.get("quotes") or []


# ---------- gold-api.com (Spotpreis Gold/Silber live, direkt in EUR) ----------
def goldapi_price(symbol: str, cur: str = "EUR") -> tuple[int, float]:
    j = _get(f"https://api.gold-api.com/price/{symbol}/{cur}")
    ts = int(dt.datetime.fromisoformat(j["updatedAt"].replace("Z", "+00:00")).timestamp())
    return ts, float(j["price"])


# ---------- EZB: EUR/USD Referenzkurs ab 1999 ----------
def ecb_eurusd(start: str = "1999-01-01") -> list[tuple[str, float]]:
    txt = _get("https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
               {"format": "csvdata", "startPeriod": start}, timeout=60, as_json=False)
    out = []
    for row in csv.DictReader(io.StringIO(txt)):
        if row.get("OBS_VALUE"):
            out.append((row["TIME_PERIOD"], float(row["OBS_VALUE"])))
    return out


def ecb_rates(codes: list[str], start: str = "1999-01-01") -> list[tuple[str, str, float]]:
    """Mehrere Währungen auf einmal: (cur, tag, kurs) mit 1 EUR = kurs CUR."""
    key = "+".join(codes)
    txt = _get(f"https://data-api.ecb.europa.eu/service/data/EXR/D.{key}.EUR.SP00.A",
               {"format": "csvdata", "startPeriod": start}, timeout=90, as_json=False)
    out = []
    for row in csv.DictReader(io.StringIO(txt)):
        if row.get("OBS_VALUE"):
            out.append((row["CURRENCY"], row["TIME_PERIOD"], float(row["OBS_VALUE"])))
    return out


# ---------- Eurostat: HVPI (ECOICOP 2), Index 2015=100, monatlich ab 1996 ----------
def eurostat_hicp(geo: str = "DE") -> list[tuple[str, float]]:
    j = _get("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr",
             {"geo": geo, "coicop18": "TOTAL", "unit": "I15"}, timeout=60)
    times = j["dimension"]["time"]["category"]["index"]
    vals = j["value"]
    return [(t, float(vals[str(pos)])) for t, pos in sorted(times.items(), key=lambda x: x[1]) if str(pos) in vals]
