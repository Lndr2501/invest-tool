"""Rechenschicht für Website und Telegram: alles in der Währung und Einheit des Profils.

* live / unit_info: aktueller Preis je Gramm/Kilo/Unze in Wunsch-Feinheit und -Währung
* portfolio / portfolio_history: Bestand bewerten und echten Verlauf seit Kauf zeigen
* calc_future / calc_goal / calc_past: „Was könnte werden?“, „Wie viel muss ich sparen?“, „Was wäre gewesen?“
* overview: Ratgeber-Startseite in einfachen Worten
"""
from __future__ import annotations

import datetime as dt
import math
import time

import numpy as np
import pandas as pd

from . import db, forecast, markets, profiles
from .calculator import MONTH, Costs, Plan, default_costs, simulate
from .config import ASSETS
from .units import OZ, UNIT_LABEL, fineness_label, fmt_money, fmt_num, price_per

DRIFTS = {"careful": "vorsichtig", "historic": "wie bisher", "none": "gleich null (nur Schwankung)"}
_CACHE: dict = {}


def _cached(key, ttl: float, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (time.time(), val)
    return val


def clear_cache() -> None:
    _CACHE.clear()
    db._FX_CACHE.clear()


# ---------- Kurse ----------
def closes_eur(con) -> pd.DataFrame:
    """Tagesschlusskurse (EUR je Feinunze / BTC / Anteil) auf Kalendertagen, NaN vor Beginn der Historie."""
    def build():
        cols = {}
        for a in list(ASSETS):
            c = db.load_daily(con, a)["close"]
            if len(c):
                cols[a] = c
        df = pd.DataFrame(cols).sort_index()
        df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="D")).ffill()
        for a in ASSETS:  # Märkte ohne Daten als leere Spalte, damit Zugriffe nicht scheitern
            if a not in df:
                df[a] = np.nan
        return df
    return _cached(("closes", tuple(ASSETS)), 900, build)


def fx_daily(con, cur: str) -> pd.Series:
    """1 EUR = x CUR auf Kalendertagen (ffill). Für EUR leer."""
    def build():
        s = db.fx_rates(con, cur)
        if s.empty:
            return s
        return s.reindex(pd.date_range(s.index.min(), pd.Timestamp.today().normalize(), freq="D")).ffill()
    return _cached(f"fx:{cur}", 1800, build) if cur != "EUR" else pd.Series(dtype=float)


def fx_at(con, cur: str, day) -> float:
    if cur == "EUR":
        return 1.0
    s = fx_daily(con, cur)
    day = pd.Timestamp(day).normalize()
    if day <= s.index[0]:
        return float(s.iloc[0])
    if day >= s.index[-1]:
        return float(s.iloc[-1])
    return float(s.loc[day])


def fx_on(con, cur: str, index: pd.DatetimeIndex) -> np.ndarray:
    if cur == "EUR":
        return np.ones(len(index))
    s = fx_daily(con, cur)
    return s.reindex(index, method="ffill").bfill().fillna(float(s.iloc[-1])).values


def _live_one(con, a: str) -> dict:
    now = time.time()
    closes = closes_eur(con)
    df = db.load_5m(con, a, since=int(now - 4 * 86400))
    price, ts, src, r24 = None, None, None, None
    if not df.empty:
        c = df["close"]
        price, ts, src = float(c.iloc[-1]), int(df["ts"].iloc[-1]), str(df["src"].iloc[-1])
        p24 = c.asof(c.index[-1] - pd.Timedelta(hours=24))
        r24 = price / p24 - 1 if p24 == p24 and p24 else None
    if ASSETS[a].get("spot"):
        sp = db.load_spot(con, a, since=int(now - 3 * 3600))
        if not sp.empty:
            price, ts, src = float(sp["price_eur"].iloc[-1]), int(sp["ts"].iloc[-1]), "Spotpreis gold-api.com"
    s = closes[a].dropna() if a in closes else pd.Series(dtype=float)
    if price is None:
        if s.empty:
            return {"eur": None, "ts": None, "src": None, "change": {}, "stale": True}
        price, ts, src = float(s.iloc[-1]), int(s.index[-1].timestamp()), "Tagesschluss"
    ch = {"24h": r24}
    for key, days in (("7d", 7), ("30d", 30), ("1y", 365), ("5y", 1826)):
        ref = s.asof(s.index[-1] - pd.Timedelta(days=days)) if len(s) else None
        ch[key] = price / ref - 1 if ref == ref and ref else None
    return {"eur": price, "ts": ts, "src": src, "change": ch,
            "stale": now - ts > (3 * 3600 if ASSETS[a]["always_open"] else 4 * 86400)}


class _Live(dict):
    """Aktueller Preis je Markt in EUR – wird erst beim Zugriff berechnet (60 s zwischengespeichert)."""

    def __init__(self, con):
        super().__init__()
        self._con = con

    def __missing__(self, a):
        if a not in ASSETS:
            raise KeyError(a)
        v = _cached(("live", a), 60, lambda: _live_one(self._con, a))
        self[a] = v
        return v


def live(con) -> dict:
    """Aktueller Preis je Asset in EUR (Spot für Metalle, sonst letzte 5-min-Kerze) + Veränderungen."""
    return _Live(con)


def unit_info(settings: dict, asset: str) -> tuple[float, str, str]:
    """Faktor auf den Basispreis, lange Beschriftung, kurze Einheit."""
    a = ASSETS[asset]
    if markets.is_metal(asset):
        m = settings.get(markets.METALS[asset]) or {"unit": "oz", "fineness": 999.9}
        unit, fin = m["unit"], m["fineness"]
        f = price_per(1.0, unit, fin)
        word = {"g": "1 Gramm", "kg": "1 Kilo", "oz": "1 Unze"}[unit]
        return f, f"{word} {a['name']} ({fineness_label(fin)})", f"/{unit}"
    if a["kind"] == "crypto":
        return 1.0, f"1 {a['name']}", f"/{a.get('short') or asset}"
    return 1.0, f"1 {a['unit']} {a.get('short') or a['name']}", f"/{a['unit']}"


def price_display(con, profile: dict, asset: str) -> dict:
    s = profile["settings"]
    cur = s["currency"]
    lv = live(con)[asset]
    f, label, short = unit_info(s, asset)
    value = lv["eur"] * f * fx_at(con, cur, pd.Timestamp.today()) if lv["eur"] is not None else None
    return {"asset": asset, "name": ASSETS[asset]["name"], "value": value, "text": fmt_money(value, cur),
            "label": label, "short": short, "ts": lv["ts"], "src": lv["src"], "stale": lv["stale"],
            "change": lv["change"]}


# ---------- Bestand ----------
def _buy_mult(asset: str) -> float:
    c = Costs(**markets.costs_for(asset))
    return (1 - c.buy_fee) / (1 + c.spread / 2)


def _n(x: float, digits: int = 3) -> str:
    t = fmt_num(float(x), digits)
    return t.rstrip("0").rstrip(",") if "," in t else t


def holding_title(h: dict) -> tuple[str, str]:
    a = h["asset"]
    m = ASSETS.get(a) or {"name": a, "unit": "Anteil", "units": "Anteile", "kind": "etf"}
    if markets.is_metal(a):
        metal = m["name"]
        fine_g = h["quantity"] * OZ
        cnt = _n(h["count"], 2)
        if h["form"] == "coin":
            size = ""
            if h["unit"] == "oz" and h["weight"] != 1.0:
                size = {0.5: " 1/2 oz", 0.25: " 1/4 oz", 0.1: " 1/10 oz", 0.05: " 1/20 oz"}.get(h["weight"], "")
            elif h["unit"] == "oz":
                size = " 1 oz"
            title = f"{cnt} × {h['label']}{size}"
        else:
            kind = {"bar": "Barren", "jewelry": "Schmuck", "other": metal}.get(h["form"], metal)
            w = _n(h["weight"])
            if h["form"] == "bar":
                title = f"{cnt} × Barren {w} {h['unit']}"
            else:
                title = f"{kind} {w} {h['unit']}" + (f" ({cnt} Stück)" if h["count"] != 1 else "")
            if h.get("label"):
                title = f"{title} – {h['label']}"
        sub = f"{metal} {fineness_label(h['fineness'])} · {_n(fine_g, 2)} g reines {metal}"
        return title, sub
    q = h["quantity"]
    if m["kind"] == "crypto":
        return f"{_n(q, 8)} {m.get('short') or a}", h.get("label") or m["name"]
    word = m["unit"] if q == 1 else m.get("units", m["unit"])
    return f"{_n(q, 3)} {word} {m.get('short') or m['name']}", h.get("label") or markets.KINDS[m["kind"]]["label"]


def _to_cur(con, amount: float, from_cur: str, to_cur: str, day) -> float:
    if from_cur == to_cur:
        return amount
    return amount / fx_at(con, from_cur, day) * fx_at(con, to_cur, day)


def plan_executions(con, plan: dict, until: pd.Timestamp | None = None) -> list[dict]:
    """Sparplan-Käufe: monatlich ab Startdatum, Kurs am Ausführungstag, mit Kaufkosten."""
    closes = closes_eur(con)[plan["asset"]]
    first = closes.first_valid_index()
    until = until or closes.index[-1]
    end = min(pd.Timestamp(plan["end"]), until) if plan.get("end") else until
    out = []
    for d in pd.date_range(pd.Timestamp(plan["start"]), end, freq=pd.DateOffset(months=1)):
        if d < first:
            continue
        price = float(closes.asof(d))
        eur = plan["amount"] / fx_at(con, plan["cur"], d)
        out.append({"date": d, "amount": plan["amount"], "eur": eur, "units": eur * _buy_mult(plan["asset"]) / price})
    return out


def _price_at(con, asset: str, day) -> float:
    return float(closes_eur(con)[asset].asof(pd.Timestamp(day)))


def portfolio(con, profile: dict) -> dict:
    s = profile["settings"]
    cur = s["currency"]
    rate = fx_at(con, cur, pd.Timestamp.today())
    lv = live(con)
    closes = closes_eur(con)
    positions = []
    by_asset: dict[str, dict] = {}
    total = cost_known = value_known = 0.0
    missing_date = 0
    for h in profiles.list_holdings(con, profile["id"]):
        a = h["asset"]
        if a not in ASSETS or lv[a]["eur"] is None:
            continue
        by_asset.setdefault(a, {"value": 0.0, "qty": 0.0})
        value = h["quantity"] * lv[a]["eur"] * rate
        cost = None
        if h["buy_price"] is not None:
            day = h["buy_date"] or pd.Timestamp.today()
            cost = _to_cur(con, h["buy_price"], h["buy_cur"] or cur, cur, day)
            cost_known += cost
            value_known += value
        if not h["buy_date"]:
            missing_date += 1
        title, sub = holding_title(h)
        positions.append({"kind": "holding", "id": h["id"], "asset": a, "title": title, "sub": sub, "value": value,
                          "cost": cost, "gain": (value - cost) if cost is not None else None,
                          "gain_pct": (value / cost - 1) if cost else None, "buy_date": h["buy_date"],
                          "raw": h})
        by_asset[a]["value"] += value
        by_asset[a]["qty"] += h["quantity"]
        total += value
    plans_out = []
    for p in profiles.list_plans(con, profile["id"]):
        if p["asset"] not in ASSETS or lv[p["asset"]]["eur"] is None:
            continue
        by_asset.setdefault(p["asset"], {"value": 0.0, "qty": 0.0})
        ex = plan_executions(con, p)
        units = sum(e["units"] for e in ex)
        invested = sum(_to_cur(con, e["amount"], p["cur"], cur, e["date"]) for e in ex)
        value = units * lv[p["asset"]]["eur"] * rate
        running = not p["end"] or pd.Timestamp(p["end"]) >= pd.Timestamp.today()
        plans_out.append({**p, "executions": len(ex), "units": units, "invested": invested, "value": value,
                          "running": running})
        if ex:
            positions.append({"kind": "plan", "id": p["id"], "asset": p["asset"],
                              "title": f"Sparplan {ASSETS[p['asset']]['name']}",
                              "sub": f"{fmt_money(p['amount'], p['cur'])} im Monat seit "
                                     f"{pd.Timestamp(p['start']).strftime('%m/%Y')} · {len(ex)} Käufe",
                              "value": value, "cost": invested, "gain": value - invested,
                              "gain_pct": value / invested - 1 if invested else None, "buy_date": p["start"]})
            cost_known += invested
            value_known += value
            by_asset[p["asset"]]["value"] += value
            by_asset[p["asset"]]["qty"] += units
            total += value
    # Veränderung des heutigen Bestands (gleiche Mengen, alte Kurse und Wechselkurse)
    change = {}
    if total > 0:
        r24 = sum(v["value"] * (1 - 1 / (1 + (lv[a]["change"].get("24h") or 0))) for a, v in by_asset.items())
        change["24h"] = {"abs": r24, "pct": r24 / (total - r24) if total - r24 else None}
        for key, days in (("7d", 7), ("30d", 30), ("1y", 365)):
            day = closes.index[-1] - pd.Timedelta(days=days)
            then = 0.0
            for a in by_asset:
                q = by_asset[a]["qty"]
                if q and closes[a].asof(day) == closes[a].asof(day):
                    then += q * float(closes[a].asof(day)) * fx_at(con, cur, day)
            if then:
                change[key] = {"abs": total - then, "pct": total / then - 1}
    alloc = [{"asset": a, "name": ASSETS[a]["name"], "value": v["value"], "share": v["value"] / total,
              "qty": v["qty"], "fine_g": v["qty"] * OZ if markets.is_metal(a) else None, "kind": ASSETS[a]["kind"]}
             for a, v in by_asset.items() if v["value"] > 0] if total else []
    alloc.sort(key=lambda x: -x["value"])
    return {"currency": cur, "total": total, "cost_known": cost_known, "value_known": value_known,
            "gain": value_known - cost_known if cost_known else None,
            "gain_pct": value_known / cost_known - 1 if cost_known else None,
            "change": change, "alloc": alloc, "positions": positions, "plans": plans_out,
            "missing_date": missing_date, "empty": not positions and not plans_out}


def _start_holdings(con, profile: dict) -> dict[str, tuple[float, float, int]]:
    """Bestand für den Rechner: Asset → (Wert heute, Einstand, Tage seit Kauf) in Profilwährung."""
    pf = portfolio(con, profile)
    today = pd.Timestamp.today().normalize()
    agg: dict[str, list[float]] = {}
    for p in pf["positions"]:
        days = (today - pd.Timestamp(p["buy_date"])).days if p.get("buy_date") else 0
        basis = p["cost"] if p["cost"] is not None else p["value"]
        v = agg.setdefault(p["asset"], [0.0, 0.0, 0.0])
        v[0] += p["value"]
        v[1] += basis
        v[2] += p["value"] * days  # wertgewichtetes Alter
    return {a: (v[0], v[1], int(v[2] / v[0]) if v[0] else 0) for a, v in agg.items()}


def portfolio_history(con, profile: dict) -> dict:
    """Echter Verlauf: Wert und Einstand je Tag seit dem ersten Kauf (Positionen mit Kaufdatum + Sparpläne)."""
    cur = profile["settings"]["currency"]
    closes = closes_eur(con)
    events = []  # (tag, asset, menge, einstand in cur)
    skipped = 0
    for h in profiles.list_holdings(con, profile["id"]):
        if h["asset"] not in ASSETS or closes[h["asset"]].first_valid_index() is None:
            continue
        if not h["buy_date"]:
            skipped += 1
            continue
        d = max(pd.Timestamp(h["buy_date"]), closes[h["asset"]].first_valid_index())
        if h["buy_price"] is not None:
            cost = _to_cur(con, h["buy_price"], h["buy_cur"] or cur, cur, d)
        else:
            cost = h["quantity"] * _price_at(con, h["asset"], d) * fx_at(con, cur, d)
        events.append((d, h["asset"], h["quantity"], cost))
    for p in profiles.list_plans(con, profile["id"]):
        if p["asset"] not in ASSETS or closes[p["asset"]].first_valid_index() is None:
            continue
        for e in plan_executions(con, p):
            events.append((e["date"], p["asset"], e["units"], _to_cur(con, e["amount"], p["cur"], cur, e["date"])))
    if not events:
        return {"empty": True, "skipped": skipped}
    start = min(e[0] for e in events).normalize()
    idx = pd.date_range(start, closes.index[-1], freq="D")
    held = list(dict.fromkeys(e[1] for e in events))
    qty = pd.DataFrame(0.0, index=idx, columns=held)
    inv = pd.Series(0.0, index=idx)
    for d, a, q, c in events:
        d = d.normalize()
        if d > idx[-1]:
            continue
        qty.loc[d, a] += q
        inv.loc[d] += c
    qty, inv = qty.cumsum(), inv.cumsum()
    fx = fx_on(con, cur, idx)
    px = closes[held].reindex(idx).fillna(0.0)
    per_asset = qty * px.values * fx[:, None]
    value = per_asset.sum(axis=1)
    # heutiger Punkt mit Live-Kursen
    lv = live(con)
    today = pd.Timestamp.today().normalize()
    if today > idx[-1]:
        rate = fx_at(con, cur, today)
        last_q = qty.iloc[-1]
        row = {a: float(last_q[a]) * lv[a]["eur"] * rate for a in held}
        per_asset.loc[today] = row
        value.loc[today] = sum(row.values())
        inv.loc[today] = inv.iloc[-1]
    step = 1 if len(value) <= 500 else 7 if len(value) <= 3500 else 30
    keep = list(range(0, len(value), step))
    if keep[-1] != len(value) - 1:
        keep.append(len(value) - 1)
    v = value.iloc[keep]
    peak = value.cummax()
    dd = value / peak - 1
    trough = dd.idxmin()
    used = [a for a in held if per_asset[a].abs().sum() > 0]
    return {"empty": False, "skipped": skipped, "currency": cur,
            "dates": [d.strftime("%Y-%m-%d") for d in v.index], "value": v.round(2).tolist(),
            "invested": inv.iloc[keep].round(2).tolist(),
            "by_asset": {a: per_asset[a].iloc[keep].round(2).tolist() for a in used},
            "start": start.strftime("%Y-%m-%d"), "final": float(value.iloc[-1]), "invested_total": float(inv.iloc[-1]),
            "max_dd": float(dd.min()), "max_dd_date": trough.strftime("%Y-%m-%d")}


# ---------- Rechner ----------
def _weights(req: dict) -> dict[str, float]:
    w = {a: float(v) for a, v in (req.get("weights") or {}).items() if a in ASSETS and float(v or 0) > 0}
    tot = sum(w.values())
    return {a: v / tot for a, v in w.items()} if tot else {}


def _costs(req: dict) -> dict[str, Costs]:
    return default_costs() if req.get("costs", True) else {a: Costs() for a in ASSETS}


def _known(con, a: str) -> bool:
    return a in ASSETS and closes_eur(con)[a].first_valid_index() is not None


def _infl_pa(con, geo: str, years: int = 10) -> float:
    h = db.load_hicp(con, geo)
    if len(h) < 13:
        return 0.02
    last = h.index[-1]
    past = last - 12 * years
    if past not in h.index:
        past = h.index[0]
        years = (last - past).n / 12
    return float((h[last] / h[past]) ** (1 / years) - 1)


def _tax_args(profile: dict, req: dict) -> dict:
    s = profile["settings"]
    tax = s["tax"] if req.get("tax", True) else "none"
    return {"tax": tax, "personal_rate": s["personal_rate"], "flat_rate": s["flat_rate"],
            "allowance": float(s.get("allowance", 1000.0))}


def _tax_label(t: dict) -> str:
    return {"de": "Steuer Deutschland (vereinfacht)", "flat": f"pauschal {fmt_num(t['flat_rate'] * 100, 0)} % auf Gewinne",
            "none": "ohne Steuern"}[t["tax"]]


# Obergrenze für den angenommenen Trend pro Jahr im Modus „vorsichtig“ (lange Zeiträume sonst unrealistisch).
# Kernmärkte fest, alle anderen nach Art (markets.KINDS[...]["cap_pa"]).
CAREFUL_CAP_PA = {"BTC": 0.15, "GOLD": 0.05, "SILVER": 0.04, "MSCIW": 0.07}


def cap_pa(a: str) -> float:
    return CAREFUL_CAP_PA.get(a, markets.cap_pa(a))


def _net_label(tax: dict, req: dict) -> str:
    """Kurzer Hinweis, wofür die Endzahl steht (Verkauf inkl. Kosten/Steuern)."""
    if tax["tax"] != "none":
        return "nach Verkauf und Steuern"
    return "nach Verkaufskosten" if req.get("costs", True) else ""


def _drift(req: dict, assets: list[str], con, R: np.ndarray) -> tuple[np.ndarray, str]:
    mode = req.get("drift", "careful")
    if mode == "historic":
        return np.ones(len(assets)), DRIFTS["historic"]
    if mode == "none":
        return np.zeros(len(assets)), DRIFTS["none"]
    # vorsichtig: Trend-Annahme aus dem Backtest, zusätzlich gedeckelt (Log-Trend pro Kalendertag)
    out = []
    mean = R.mean(axis=0)
    for i, a in enumerate(assets):
        bt = db.kv_get(con, f"backtest:{a}") or {}
        d = float((bt.get("best_drift") or {}).get("drift", 0.5))
        cap = math.log1p(cap_pa(a)) / 365.25
        if mean[i] > 0:
            d = min(d, cap / mean[i])
        out.append(d)
    return np.array(out), DRIFTS["careful"]


def _sim_inputs(con, assets: list[str]) -> tuple[np.ndarray, str]:
    closes = closes_eur(con)[assets].dropna()
    if len(closes) < 365:
        short = max(assets, key=lambda a: closes_eur(con)[a].first_valid_index() or pd.Timestamp.max)
        raise ValueError(f"Für {ASSETS[short]['name']} gibt es noch zu wenig Kursdaten für eine Prognose.")
    return forecast.log_returns(closes), closes.index[0].strftime("%Y-%m-%d")


def calc_future(con, profile: dict, req: dict) -> dict:
    s = profile["settings"]
    cur = s["currency"]
    years = int(min(max(float(req.get("years") or 10), 1), 40))
    H = int(round(years * 365.25))
    w = _weights(req)
    start = _start_holdings(con, profile) if req.get("use_holdings") else {}
    initial = max(float(req.get("initial") or 0), 0.0)
    monthly = max(float(req.get("monthly") or 0), 0.0)
    if not w and not start:
        raise ValueError("Bitte mindestens eine Anlage auswählen.")
    if not w:
        w = {a: v[0] for a, v in start.items()}
        tot = sum(w.values())
        w = {a: v / tot for a, v in w.items()}
    if initial + monthly <= 0 and not start:
        raise ValueError("Bitte einen Startbetrag oder eine Sparrate eingeben.")
    assets = list(dict.fromkeys(list(w) + list(start)))
    R, hist_from = _sim_inputs(con, assets)
    drift, drift_label = _drift(req, assets, con, R)
    n = forecast.sims_for(H, len(assets), budget=1.5e7, cap=2000)
    P = forecast.bootstrap_paths(R, H, n, drift=drift, seed=7)
    paths = {a: P[i] for i, a in enumerate(assets)}
    infl = _infl_pa(con, s["inflation_geo"]) if req.get("inflation", True) else 0.0
    tax = _tax_args(profile, req)
    wd = req.get("withdraw") or {}
    plan = Plan(weights={a: w.get(a, 0.0) for a in assets}, horizon_days=H, initial=initial, monthly=monthly,
                contrib_months=int(req["contrib_years"] * 12) if req.get("contrib_years") else None,
                withdraw_monthly=float(wd.get("monthly") or 0),
                withdraw_from_month=int(float(wd["from_year"]) * 12) if wd.get("monthly") and wd.get("from_year") else None,
                rebalance=req.get("rebalance") or "none", costs=_costs(req), inflation_pa=infl,
                goal=float(req["goal"]) if req.get("goal") else None, start=start, **tax)
    year_days = [int(round(y * 365.25)) for y in range(1, years + 1)]
    res = simulate(plan, paths, points=260, extra_days=year_days)
    today = pd.Timestamp.today().normalize()
    days = res["fan"]["days"]
    dates = [(today + pd.Timedelta(days=d)).strftime("%Y-%m-%d") for d in days]
    vals = res["per_sim"]["value"]
    key = "after_tax" if tax["tax"] != "none" else "payout"
    rows = []
    for y, d in zip(range(1, years + 1), year_days):
        k = days.index(min(d, H))
        col = vals[:, k]
        rows.append({"year": today.year + y, "n": y, "invested": res["fan"]["invested"][k],
                     "p5": float(np.percentile(col, 5)), "p50": float(np.percentile(col, 50)),
                     "p95": float(np.percentile(col, 95))})
    end = res[key]
    contrib = res["invested"] - res["start_value"]
    p50 = end[50]
    text = []
    if res["start_value"]:
        text.append(f"Du startest mit deinem Bestand von {fmt_money(res['start_value'], cur)}"
                    + (f" und zahlst {fmt_money(contrib, cur)} dazu." if contrib > 0 else "."))
    else:
        text.append(f"Du zahlst insgesamt {fmt_money(contrib, cur)} ein.")
    text.append(f"Im mittleren Verlauf hast du nach {years} Jahr{'en' if years > 1 else ''} etwa "
                f"{fmt_money(p50, cur)}. Das ist {'mehr' if p50 >= res['invested'] else 'weniger'} als eingesetzt "
                f"(Unterschied {fmt_money(p50 - res['invested'], cur)}).")
    text.append(f"Die Hälfte der durchgespielten Verläufe endet zwischen {fmt_money(end[25], cur)} und "
                f"{fmt_money(end[75], cur)}. In 9 von 10 Fällen landest du zwischen {fmt_money(end[5], cur)} und "
                f"{fmt_money(end[95], cur)}.")
    text.append(f"In {fmt_num(res['p_loss'] * 100, 0)} % der Verläufe hast du am Ende weniger als eingesetzt.")
    text.append(f"Zwischendurch fällt der Wert im mittleren Verlauf einmal {fmt_num(-res['max_dd'][50] * 100, 0)} % "
                f"unter sein bisheriges Hoch. Im schlechten Fall (1 von 20) sind es {fmt_num(-res['max_dd'][5] * 100, 0)} %.")
    if infl:
        text.append(f"In heutigem Geld ist der mittlere Verlauf etwa "
                    f"{fmt_money(res['real_after_tax'][50] if key == 'after_tax' else p50 / res['deflator'], cur)} wert, "
                    f"wenn die Preise weiter um {fmt_num(infl * 100, 1)} % pro Jahr steigen (Inflation).")
    stress = _stress(con, plan, assets, w, today)
    return {
        "net_label": _net_label(tax, req), "currency": cur, "years": years, "invested": res["invested"], "start_value": res["start_value"],
        "contrib": contrib, "end": end, "real_end": {q: v / res["deflator"] for q, v in end.items()},
        "p_loss": res["p_loss"], "p_goal": res["p_goal"], "max_dd": res["max_dd"],
        "withdrawn": res["withdrawn"], "ran_out_share": res["ran_out_share"],
        "fan": {"dates": dates, **{f"p{q}": [round(x, 2) for x in res["fan"]["pct"][q]] for q in (5, 25, 50, 75, 95)},
                "invested": res["fan"]["invested"]},
        "rows": rows, "text": text, "stress": stress,
        "assumptions": {"drift": drift_label, "inflation_pa": infl, "n_sims": res["n"], "history_from": hist_from,
                        "costs": bool(req.get("costs", True)), "tax": _tax_label(tax),
                        "weights": {a: plan.weights[a] for a in assets}},
    }


def _stress(con, plan: Plan, assets: list[str], w: dict, today) -> dict | None:
    """Härtetest: Start genau am Hoch vor dem größten Absturz der Hauptanlage (echte Kurse aller Anlagen)."""
    lead = max(w, key=w.get)
    closes = closes_eur(con)[assets].dropna()
    if len(closes) < 400:
        return None
    try:
        rel, peak, h = forecast.stress_path(closes, lead, plan.horizon_days)
    except Exception:
        return None
    if h < 365:
        return None
    p2 = Plan(**{**plan.__dict__, "horizon_days": h, "withdraw_from_month": None, "withdraw_monthly": 0.0})
    res = simulate(p2, {a: rel[i] for i, a in enumerate(assets)}, points=200)
    v = np.array(res["fan"]["pct"][50])
    inv = np.array(res["fan"]["invested"])
    k = int(np.argmin(v - inv))
    days = res["fan"]["days"]
    return {"lead": lead, "lead_name": ASSETS[lead]["name"], "peak": peak.strftime("%Y-%m-%d"),
            "years": round(h / 365.25, 1), "final": res["final_value"][50], "invested": res["invested"],
            "low": float(v[k]), "low_invested": float(inv[k]),
            "low_date": (peak + pd.Timedelta(days=days[k])).strftime("%Y-%m-%d"),
            "full": h >= plan.horizon_days}


def calc_goal(con, profile: dict, req: dict) -> dict:
    """Welche Sparrate braucht es für den Zielbetrag? Endwert ist linear in der Sparrate → zwei Läufe reichen."""
    s = profile["settings"]
    cur = s["currency"]
    goal = float(req.get("goal") or 0)
    if goal <= 0:
        raise ValueError("Bitte einen Zielbetrag eingeben.")
    years = int(min(max(float(req.get("years") or 10), 1), 40))
    H = int(round(years * 365.25))
    w = _weights(req)
    if not w:
        raise ValueError("Bitte mindestens eine Anlage auswählen.")
    start = _start_holdings(con, profile) if req.get("use_holdings") else {}
    assets = list(dict.fromkeys(list(w) + list(start)))
    R, hist_from = _sim_inputs(con, assets)
    drift, drift_label = _drift(req, assets, con, R)
    n = forecast.sims_for(H, len(assets), budget=1.6e7, cap=2000)
    P = forecast.bootstrap_paths(R, H, n, drift=drift, seed=11)
    paths = {a: P[i] for i, a in enumerate(assets)}
    base = dict(weights={a: w.get(a, 0.0) for a in assets}, horizon_days=H, costs=_costs(req), tax="none",
                inflation_pa=0.0)
    r0 = simulate(Plan(initial=float(req.get("initial") or 0), monthly=0.0, start=start, **base), paths, points=50)
    r1 = simulate(Plan(initial=0.0, monthly=1.0, **base), paths, points=50)
    f0, f1 = r0["per_sim"]["payout"], r1["per_sim"]["payout"]
    need = np.maximum(goal - f0, 0) / np.maximum(f1, 1e-9)
    infl = _infl_pa(con, s["inflation_geo"])
    out = {"currency": cur, "goal": goal, "years": years,
           "monthly_50": float(np.percentile(need, 50)), "monthly_75": float(np.percentile(need, 75)),
           "monthly_90": float(np.percentile(need, 90)),
           "no_risk": max(goal - (float(req.get("initial") or 0) + sum(v[0] for v in start.values())), 0) / (years * 12),
           "goal_real": goal / (1 + infl) ** years, "inflation_pa": infl,
           "assumptions": {"drift": drift_label, "n_sims": int(n), "history_from": hist_from,
                           "costs": bool(req.get("costs", True)), "tax": "vor Steuern"}}
    out["text"] = [
        f"Für {fmt_money(goal, cur)} in {years} Jahren reichen in der Hälfte der Fälle etwa "
        f"{fmt_money(out['monthly_50'], cur)} im Monat.",
        f"Willst du es in 9 von 10 Fällen schaffen, plane mit {fmt_money(out['monthly_90'], cur)} im Monat.",
        f"Legst du das Geld nur zur Seite, ohne es anzulegen, brauchst du {fmt_money(out['no_risk'], cur)} im Monat.",
        f"Wegen der Inflation (steigende Preise) kaufst du mit {fmt_money(goal, cur)} in {years} Jahren nur so viel "
        f"wie heute mit {fmt_money(out['goal_real'], cur)}.",
    ]
    return out


def calc_past(con, profile: dict, req: dict) -> dict:
    """„Was wäre gewesen?“ – echter Kursverlauf in Profilwährung (mit historischen Wechselkursen)."""
    s = profile["settings"]
    cur = s["currency"]
    w = _weights(req)
    if not w:
        raise ValueError("Bitte mindestens eine Anlage auswählen.")
    initial = max(float(req.get("initial") or 0), 0.0)
    monthly = max(float(req.get("monthly") or 0), 0.0)
    if initial + monthly <= 0:
        raise ValueError("Bitte einen Startbetrag oder eine Sparrate eingeben.")
    closes_all = closes_eur(con)
    closes = closes_all[list(w)].dropna()
    want = pd.Timestamp(req.get("start") or (pd.Timestamp.today() - pd.DateOffset(years=10)))
    notes = []
    if want < closes.index[0]:
        late = max(w, key=lambda a: closes_all[a].first_valid_index())
        notes.append(f"Kurse für {ASSETS[late]['name']} gibt es erst ab {closes.index[0].strftime('%m/%Y')}, "
                     f"deshalb beginnt die Rechnung dort.")
        want = closes.index[0]
    closes = closes[closes.index >= want]
    if len(closes) < 40:
        raise ValueError("Der Zeitraum ist zu kurz. Bitte wähle ein früheres Startdatum.")
    fx = fx_on(con, cur, closes.index)
    px = closes.values * fx[:, None]
    H = len(closes) - 1
    tax = _tax_args(profile, req)
    costs = _costs(req)
    ev = np.unique(np.round(np.arange(0, H / MONTH + 1e-9) * MONTH).astype(int))
    ev = ev[ev < H]
    year_ends = [i for i in range(1, H + 1) if closes.index[i].year != closes.index[i - 1].year]

    def run(weights: dict) -> dict:
        paths = {}
        for a in weights:
            col = px[:, list(w).index(a)] if a in w else closes_all[a].reindex(closes.index).values * fx
            paths[a] = (col / col[0])[None, :]
        return simulate(Plan(weights=weights, horizon_days=H, initial=initial, monthly=monthly, costs=costs,
                             inflation_pa=0.0, **tax), paths, points=400, extra_days=year_ends)

    res = run(w)
    days = res["fan"]["days"]
    value = np.array(res["fan"]["pct"][50])
    inv = np.array(res["fan"]["invested"])
    dates = [closes.index[d].strftime("%Y-%m-%d") for d in days]
    key = "after_tax" if tax["tax"] != "none" else "payout"
    final = res[key][50]
    # Tiefpunkt: größter Abstand unter dem Eingezahlten und größter Rückgang vom Hoch
    rel = np.where(inv > 0, value / inv - 1, 0)
    k_rel = int(np.argmin(rel))
    peak = np.maximum.accumulate(value)
    dd = np.where(peak > 0, value / peak - 1, 0)
    k_dd = int(np.argmin(dd))
    # Kaufkraft: jede Einzahlung mit der Inflation bis heute hochgerechnet
    hicp = db.load_hicp(con, s["inflation_geo"])
    real_needed = None
    if not hicp.empty:
        hs = hicp.copy()
        hs.index = hs.index.to_timestamp()
        h_end = float(hs.iloc[-1])
        tot = 0.0
        for m, d in enumerate(ev):
            amt = (initial if m == 0 else 0.0) + monthly
            h_d = hs.asof(closes.index[d])
            tot += amt * (h_end / float(h_d) if h_d == h_d else 1.0)
        real_needed = tot
    # Vergleich: alles in eine Anlage (gleiche Einzahlungen)
    compare = []
    bench = list(dict.fromkeys(list(w) + list(req.get("compare") or []) + ["MSCIW", "GOLD", "BTC"]))
    for a in [x for x in bench if x in ASSETS][:7]:
        fv = closes_all[a].first_valid_index()
        if fv is None or fv > closes.index[0]:
            compare.append({"asset": a, "name": ASSETS[a]["name"], "final": None})
            continue
        r = run({a: 1.0})
        compare.append({"asset": a, "name": ASSETS[a]["name"], "final": r[key][50]})
    rows = []
    for d in year_ends + ([H] if H not in year_ends else []):
        k = days.index(d)
        last = d == H
        rows.append({"date": closes.index[d].strftime("%Y-%m-%d"),
                     "label": "heute" if last else f"Ende {closes.index[d - 1].year}",
                     "invested": float(inv[k]), "value": float(value[k])})
    start_s = closes.index[0].strftime("%d.%m.%Y")
    yrs = H / 365.25
    text = [f"Start am {start_s}: Du hättest {fmt_money(res['invested'], cur)} eingezahlt"
            + (f" ({fmt_money(initial, cur)} zu Beginn" if initial else " (")
            + (f"{', dann ' if initial else ''}{fmt_money(monthly, cur)} im Monat)" if monthly else ")") + ".",
            f"Heute wäre das {fmt_money(final, cur)} wert: "
            f"{'ein Plus' if final >= res['invested'] else 'ein Minus'} von {fmt_money(abs(final - res['invested']), cur)} "
            f"({'+' if final >= res['invested'] else '−'}{fmt_num(abs(final / res['invested'] - 1) * 100, 0)} %)."]
    if rel[k_rel] < -0.005:
        text.append(f"Der schwierigste Moment: Am {pd.Timestamp(dates[k_rel]).strftime('%d.%m.%Y')} war dein Geld "
                    f"{fmt_num(-rel[k_rel] * 100, 0)} % weniger wert, als du eingezahlt hattest.")
    text.append(f"Größter Absturz vom Hoch: {fmt_num(-dd[k_dd] * 100, 0)} % unter dem bisherigen Höchststand "
                f"(Tiefpunkt {pd.Timestamp(dates[k_dd]).strftime('%m/%Y')}).")
    if real_needed:
        text.append(f"Nur um die steigenden Preise auszugleichen, hätte dein Geld heute {fmt_money(real_needed, cur)} "
                    f"wert sein müssen (Inflation {s['inflation_geo']}).")
    return {"net_label": _net_label(tax, req), "currency": cur, "start": closes.index[0].strftime("%Y-%m-%d"), "end": closes.index[-1].strftime("%Y-%m-%d"),
            "years": round(yrs, 1), "invested": res["invested"], "final": final, "final_pre_tax": res["payout"][50],
            "tax": res["tax"][50], "real_needed": real_needed, "compare": compare,
            "worst_rel": float(rel[k_rel]), "worst_rel_date": dates[k_rel], "max_dd": float(dd[k_dd]),
            "max_dd_date": dates[k_dd], "cagr": (final / res["invested"]) ** (1 / yrs) - 1 if not monthly and yrs > 0 else None,
            "series": {"dates": dates, "value": value.round(2).tolist(), "invested": inv.round(2).tolist()},
            "rows": rows, "text": text, "notes": notes,
            "assumptions": {"costs": bool(req.get("costs", True)), "tax": _tax_label(tax),
                            "fx": cur != "EUR"}}


# ---------- Märkte ----------
def market(con, profile: dict, asset: str, rng: str = "1y") -> dict:
    s = profile["settings"]
    cur = s["currency"]
    f, label, short = unit_info(s, asset)
    now = time.time()
    if rng in ("1d", "1w", "1m"):
        since = now - {"1d": 86400, "1w": 7 * 86400, "1m": 31 * 86400}[rng]
        df = db.load_5m(con, asset, since=int(since))
        c = df["close"]
        if rng == "1w":
            c = c.resample("30min").last().dropna()
        elif rng == "1m":
            c = c.resample("2h").last().dropna()
        fmt = "%Y-%m-%dT%H:%M:00Z"  # 5-min-Daten sind UTC → Browser zeigt Ortszeit
        if len(c) < 10:  # zu wenig 5-min-Daten → Tageskurse
            c = closes_eur(con)[asset].dropna()
            c = c[c.index >= pd.Timestamp.fromtimestamp(since).normalize()]
            fmt = "%Y-%m-%d"
        idx = c.index
    else:
        c = closes_eur(con)[asset].dropna()
        if rng != "max":
            years = {"1y": 1, "5y": 5, "10y": 10}[rng]
            c = c[c.index >= c.index[-1] - pd.DateOffset(years=years)]
        if len(c) > 1500:
            c = c.resample("W").last().dropna()
        idx = c.index
        fmt = "%Y-%m-%d"
    fx = fx_on(con, cur, idx.normalize())
    vals = c.values * f * fx
    out = {"asset": asset, "name": ASSETS[asset]["name"], "label": label, "short": short, "currency": cur, "range": rng,
           "info": markets.public(asset),
           "dates": [d.strftime(fmt) for d in idx], "values": np.round(vals, 4).tolist()}
    if rng in ("1y", "5y", "10y", "max"):
        full = closes_eur(con)[asset].dropna()
        ma = full.rolling(200).mean().reindex(idx)
        out["ma200"] = [None if x != x else round(float(x * f * fx[i]), 4) for i, x in enumerate(ma.values)]
    if len(vals):
        out["first"], out["last"] = float(vals[0]), float(vals[-1])
        out["change"] = float(vals[-1] / vals[0] - 1) if vals[0] else None
        out["high"], out["low"] = float(np.max(vals)), float(np.min(vals))
    return out


def yearly_returns(con, asset: str) -> list[dict]:
    c = closes_eur(con)[asset].dropna()
    ye = c.resample("YE").last()
    first = c.iloc[0]
    out = []
    prev = first
    for d, v in ye.items():
        if d.year == c.index[0].year and c.index[0].month > 1:
            prev = v
            continue
        out.append({"year": d.year, "ret": float(v / prev - 1), "partial": d.year == c.index[-1].year})
        prev = v
    return out


def drawdowns(con, asset: str, top: int = 3) -> list[dict]:
    """Größte Abstürze: Hoch → Tief → wieder erholt (oder noch nicht)."""
    c = closes_eur(con)[asset].dropna()
    peak = c.cummax()
    dd = c / peak - 1
    out = []
    used = pd.Series(False, index=c.index)
    for _ in range(top):
        cand = dd[~used]
        if cand.empty or cand.min() > -0.1:
            break
        t = cand.idxmin()
        p = c[:t].idxmax()
        rec = c[t:][c[t:] >= c[p]]
        r = rec.index[0] if len(rec) else None
        used[p:(r or c.index[-1])] = True
        out.append({"peak": p.strftime("%Y-%m-%d"), "trough": t.strftime("%Y-%m-%d"), "depth": float(dd[t]),
                    "recovered": r.strftime("%Y-%m-%d") if r is not None else None,
                    "months_down": round((t - p).days / 30.44), "months_back": round((r - t).days / 30.44) if r is not None else None})
    return out


# ---------- Ratgeber ----------
TREND_TEXT = {
    "stark aufwärts": "steigt seit Monaten deutlich",
    "aufwärts": "steigt eher",
    "seitwärts": "bewegt sich ohne klare Richtung",
    "abwärts": "fällt eher",
    "stark abwärts": "fällt seit Wochen deutlich",
}
VOL_TEXT = {"ruhig": "ruhig", "normal": "normal", "wild": "unruhig"}


def asset_story(con, asset: str, ana: dict, change: dict) -> list[str]:
    name = ASSETS[asset]["name"]
    out = []
    t = ana.get("trend") or {}
    c30 = change.get("30d")
    if t.get("label"):
        s = f"{name} {TREND_TEXT.get(t['label'], t['label'])}"
        if c30 is not None:
            s += f" ({'+' if c30 >= 0 else '−'}{fmt_num(abs(c30) * 100, 0)} % in 30 Tagen)"
        out.append(s + ".")
    v = ana.get("vol") or {}
    if v.get("vol30"):
        month = v["vol30"] / math.sqrt(12) * 100
        pctl = (v.get("percentile") or 0) * 100
        s = (f"Schwankung gerade: {VOL_TEXT.get(v.get('label'), v.get('label'))}, in einem typischen Monat geht der "
             f"Kurs etwa {fmt_num(month, 0)} % hoch oder runter")
        if pctl >= 80:
            s += f". Das ist unruhiger als an {fmt_num(pctl, 0)} % aller Tage seit {ana.get('history_since', '')[:4]}"
        elif pctl <= 20:
            s += f". Das ist ruhiger als an {fmt_num(100 - pctl, 0)} % aller Tage seit {ana.get('history_since', '')[:4]}"
        out.append(s + ".")
    d = ana.get("drawdown") or {}
    if d.get("current") is not None:
        cur = d["current"]
        if cur > -0.03:
            out.append("Der Kurs ist nahe am Rekordhoch.")
        else:
            ath = pd.Timestamp(d["ath_date"]).strftime("%m/%Y") if d.get("ath_date") else ""
            out.append(f"Der Kurs liegt {fmt_num(-cur * 100, 0)} % unter seinem Rekordhoch ({ath}). "
                       f"Der größte Absturz vom Hoch bisher: {fmt_num(-d.get('max_dd', 0) * 100, 0)} %.")
    return out


def _direction_word(trend: str | None, c7: float | None) -> str:
    if trend in ("stark aufwärts", "aufwärts"):
        return "steigt"
    if trend in ("stark abwärts", "abwärts"):
        return "fällt"
    if c7 is not None and abs(c7) >= 0.03:
        return "steigt" if c7 > 0 else "fällt"
    return "seitwärts"


def market_row(con, profile: dict, a: str) -> dict:
    """Eine Zeile „Was ist los?“: Preis in Profil-Einheit, Veränderung, Richtung, Unruhe, ein Satz."""
    ana = db.kv_get(con, f"analysis:{a}") or {}
    pd_ = price_display(con, profile, a)
    trend = (ana.get("trend") or {}).get("label")
    vol = (ana.get("vol") or {}).get("label")
    story = asset_story(con, a, ana, pd_["change"])
    return {**pd_, "short": ASSETS[a].get("short") or ASSETS[a]["name"], "kind": ASSETS[a]["kind"],
            "trend": trend, "vol": vol, "word": _direction_word(trend, pd_["change"].get("7d")),
            "calm": VOL_TEXT.get(vol, vol), "from_high": (ana.get("drawdown") or {}).get("current"),
            "story": story, "line": story[0] if story else ""}


def headline(rows: list[dict]) -> list[str]:
    """Zwei kurze Sätze zur Lage der beobachteten Märkte."""
    if not rows:
        return ["Du beobachtest noch keine Märkte."]
    out = []
    movers = [(abs(r["change"].get("24h") or 0), r) for r in rows if r["change"].get("24h") is not None]
    big = max(movers, key=lambda x: x[0]) if movers else None
    if big and big[0] >= 0.02:
        r = big[1]
        c = r["change"]["24h"]
        out.append(f"Größte Bewegung: {r['short']} {'+' if c >= 0 else '−'}{fmt_num(abs(c) * 100, 1)} % in 24 Stunden.")
    else:
        out.append("Ruhiger Tag: Keiner deiner Märkte hat sich in 24 Stunden um mehr als 2 % bewegt.")
    up = sum(1 for r in rows if r["word"] == "steigt")
    down = sum(1 for r in rows if r["word"] == "fällt")
    n = len(rows)
    if down > n / 2:
        out.append(f"{down} von {n} deiner Märkte fallen seit einiger Zeit; ein Problem ist das nur, "
                   f"wenn du das Geld bald brauchst.")
    elif up > n / 2:
        out.append(f"{up} von {n} deiner Märkte steigen seit einiger Zeit; solche guten Phasen enden aber auch wieder.")
    else:
        out.append("Gemischtes Bild: Manche deiner Märkte steigen, andere fallen.")
    wild = [r["short"] for r in rows if r["vol"] == "wild"]
    if wild and len(wild) >= max(2, n // 2):
        out.append("Die Kurse schwanken gerade stärker als üblich.")
    return out


def next_steps(con, profile: dict, pf: dict) -> list[dict]:
    """Was als Nächstes sinnvoll ist – nach Wichtigkeit sortiert (das erste ist der Vorschlag)."""
    s = profile["settings"]
    f = s.get("finance") or {}
    cur = s["currency"]
    out = []
    if f.get("debt"):
        out.append({"title": "Erst teure Schulden loswerden",
                    "text": f"Du hast {fmt_money(f['debt'], cur)} Schulden eingetragen. Dispo- und Ratenkredite kosten "
                            "meist mehr Zinsen, als eine Geldanlage sicher bringt.",
                    "link": "#/assistent", "action": "Mit dem Assistenten planen"})
    if f.get("expenses") and (f.get("cash") or 0) < 3 * f["expenses"]:
        need = 3 * f["expenses"] - (f.get("cash") or 0)
        out.append({"title": "Notgroschen zuerst",
                    "text": f"Leg 3 Monatsausgaben ({fmt_money(3 * f['expenses'], cur)}) als Reserve auf ein Tagesgeldkonto, "
                            f"ein Sparkonto, an das du jederzeit kommst. Dir fehlen noch etwa {fmt_money(need, cur)}, "
                            "erst danach lohnt sich das Anlegen.",
                    "link": "#/rechner/budget", "action": "Sparrate ausrechnen"})
    if pf["empty"]:
        if not f.get("goals") and not f.get("income"):
            out.append({"title": "Du fängst gerade an?",
                        "text": "Beantworte 5 kurze Fragen, dann bekommst du einen ersten Plan. Er zeigt, wie viel du "
                                "im Monat sparen kannst und was daraus werden könnte.",
                        "link": "#/start", "action": "Plan in 5 Fragen"})
        else:
            out.append({"title": "Noch nichts angelegt",
                        "text": "Trag ein, was du schon besitzt. Oder leg einen Sparplan an (jeden Monat ein fester "
                                "Betrag) und sieh, was daraus werden könnte.", "link": "#/bestand", "action": "Zum Bestand"})
    else:
        top = pf["alloc"][0] if pf["alloc"] else None
        if top and top["share"] > 0.7:
            out.append({"title": f"{fmt_num(top['share'] * 100, 0)} % in {top['name']}",
                        "text": "Fast alles steckt in einer Anlage: Fällt sie, fällt dein ganzer Bestand mit. "
                                "Im Rechner siehst du, wie viel ruhiger eine Mischung aus mehreren Anlagen sein kann.",
                        "link": "#/rechner/future?use_holdings=1", "action": "Mischung durchrechnen"})
        crypto = sum(a["share"] for a in pf["alloc"] if a.get("kind") == "crypto")
        if crypto > 0.3:
            out.append({"title": f"{fmt_num(crypto * 100, 0)} % in Krypto",
                        "text": "Krypto ist schon mehrmals um 70–80 % abgestürzt, aus 1.000 € wurden also 200–300 €. "
                                "Steck nur Geld hinein, das du jahrelang nicht brauchst.", "link": "#/maerkte/BTC",
                        "action": "Größte Abstürze ansehen"})
        if not pf["plans"]:
            out.append({"title": "Regelmäßig sparen?",
                        "text": "Mit einem Sparplan legst du jeden Monat automatisch einen festen Betrag an. "
                                "Rechne aus, was aus 50 € im Monat werden könnte.",
                        "link": "#/rechner/future?monthly=50&initial=0", "action": "Sparrate durchrechnen"})
        if pf["missing_date"]:
            out.append({"title": "Kaufdatum fehlt",
                        "text": f"Bei {pf['missing_date']} deiner Anlagen fehlt das Kaufdatum. Ohne Datum können sie "
                                "nicht im Verlauf erscheinen.", "link": "#/bestand", "action": "Kaufdatum ergänzen"})
    if not f.get("goals"):
        out.append({"title": "Wofür sparst du?",
                    "text": "Leg ein Ziel fest, zum Beispiel 10.000 € in 5 Jahren. Dann zeigt dir der Ratgeber, "
                            "ob deine Sparrate reicht.",
                    "link": "#/ziele", "action": "Ziel festlegen"})
    if not profile.get("tg_chat_id"):
        out.append({"title": "Bericht aufs Handy",
                    "text": "Verbinde die Chat-App Telegram. Dann bekommst du regelmäßig einen kurzen Bericht und nur "
                            "bei großen Kurssprüngen eine Nachricht.", "link": "#/einstellungen",
                    "action": "Telegram verbinden"})
    return out[:4]


# Angenommene Rendite pro Jahr je Anlagedauer (vorsichtig, vor Steuern) – nur für die Ziel-Ampel
GOAL_RETURN = [(3, 0.015, "Tagesgeld"), (10, 0.035, "Mischung aus Anleihen und Aktien"),
               (99, 0.05, "breit gestreute Aktien-ETFs")]


def goal_plan(goal: dict, current: float = 0.0) -> dict | None:
    """Was braucht es pro Monat für ein Ziel? Einfache Rechnung mit vorsichtiger Rendite nach Dauer."""
    amount, years = goal.get("amount"), goal.get("years")
    if not amount or not years:
        return None
    r, label = next((r, lab) for y, r, lab in GOAL_RETURN if years <= y)
    n = max(int(round(years * 12)), 1)
    i = (1 + r) ** (1 / 12) - 1
    fv_now = current * (1 + i) ** n
    factor = ((1 + i) ** n - 1) / i if i else n
    need = max(amount - fv_now, 0) / factor
    return {"monthly": need, "rate": r, "label": label, "months": n}


def goal_status(profile: dict, pf: dict) -> dict | None:
    s = profile["settings"]
    f = s.get("finance") or {}
    goals = [g for g in f.get("goals") or [] if g.get("amount") and g.get("years")]
    if not goals:
        return None
    g = goals[0]
    plan = goal_plan(g, pf["total"] if g["purpose"] in ("wealth", "retirement") else 0.0)
    planned = f.get("saving") or sum(p["amount"] for p in pf["plans"] if p["running"])
    cur = s["currency"]
    on_track = planned >= plan["monthly"] * 0.95 if planned else None
    text = (f"Für „{g['name']}“ ({fmt_money(g['amount'], cur)} in {_n(g['years'], 1)} Jahren) brauchst du etwa "
            f"{fmt_money(plan['monthly'], cur)} im Monat. Gerechnet ist mit {fmt_num(plan['rate'] * 100, 1)} % "
            f"Wertzuwachs pro Jahr ({plan['label']}).")
    if planned:
        text += f" Du hast {fmt_money(planned, cur)} im Monat eingeplant, " + ("das reicht." if on_track else "das reicht noch nicht.")
    return {"goal": g, "monthly": plan["monthly"], "planned": planned, "on_track": on_track, "text": text,
            "count": len(goals)}


def overview(con, profile: dict) -> dict:
    s = profile["settings"]
    pf = portfolio(con, profile)
    watch = [a for a in s["assets"] if _known(con, a)]
    held = [a["asset"] for a in pf["alloc"] if a["asset"] not in watch]
    rows = [market_row(con, profile, a) for a in (watch + held)[:10]]
    last_run = con.execute("SELECT MAX(ts) FROM runs WHERE job='collect' AND ok=1").fetchone()[0]
    return {"portfolio": {k: pf[k] for k in ("total", "gain", "gain_pct", "change", "alloc", "empty", "currency")},
            "headline": headline(rows), "markets": rows, "next": next_steps(con, profile, pf),
            "goal": goal_status(profile, pf), "updated": last_run,
            "inflation_1y": _infl_pa(con, s["inflation_geo"], years=1)}


def market_list(con, profile: dict) -> list[dict]:
    """Alle Märkte für die Marktübersicht (Preis in Profil-Einheit, Veränderung, Richtung)."""
    watch = set(profile["settings"]["assets"])
    out = []
    for a in ASSETS:
        if not _known(con, a):
            continue
        ana = db.kv_get(con, f"analysis:{a}") or {}
        pd_ = price_display(con, profile, a)
        trend = (ana.get("trend") or {}).get("label")
        out.append({**markets.public(a), "price": pd_["text"], "label": pd_["label"], "change": pd_["change"],
                    "word": _direction_word(trend, pd_["change"].get("7d")),
                    "calm": VOL_TEXT.get((ana.get("vol") or {}).get("label"), ""),
                    "since": ana.get("history_since"), "watched": a in watch, "stale": pd_["stale"]})
    return out


# Anzeigenamen der Richtungsmodelle aus dem Backtest (die Namen dort bleiben als Kennung gespeichert)
MODEL_TEXT = {
    "Immer steigt": "Steigt immer",
    "Momentum 3 Monate": "Momentum: Trend der letzten 3 Monate",
    "Über 200-Tage-Linie": "200-Tage-Linie: Kurs über dem Schnitt",
    "Logistische Regression": "Lernmodell (einfach)",
    "Gradient Boosting": "Lernmodell (aufwendig)",
}


def methodik(con, assets: list[str] | None = None) -> dict:
    out = {}
    for a in assets or list(ASSETS):
        if a not in ASSETS:
            continue
        bt = db.kv_get(con, f"backtest:{a}") or {}
        if not bt:
            continue
        cal = [c for c in bt.get("calibration", []) if c["method"] == "bootstrap"
               and c["drift"] == (bt.get("best_drift") or {}).get("drift", 1.0)]
        dirs = []
        for d in bt.get("direction", []):
            best = max((m for m in d["models"] if m["name"] != "Immer steigt"), key=lambda m: m["hit"], default=None)
            dirs.append({"days": d["horizon_days"], "base": d["base_rate_up"],
                         "best": best and MODEL_TEXT.get(best["name"], best["name"]),
                         "best_hit": best and best["hit"], "approved": any(m["approved"] for m in d["models"])})
        ma = None
        for d in bt.get("direction", []):
            for m in d["models"]:
                if m["name"] == "Über 200-Tage-Linie":
                    ma = {"dd": m["strategy"]["max_dd"], "bh_dd": d["buy_hold"]["max_dd"],
                          "cagr": m["strategy"]["cagr"], "bh_cagr": d["buy_hold"]["cagr"]}
            break
        out[a] = {"name": ASSETS[a]["name"], "coverage": [{"days": c["horizon"], "cov90": c["cov90"], "n": c["n_dates"]}
                                                         for c in cal],
                  "direction": dirs, "ma200": ma, "updated": bt.get("updated")}
    return out


def today_str() -> str:
    return dt.date.today().strftime("%d.%m.%Y")


__all__ = ["live", "price_display", "portfolio", "portfolio_history", "calc_future", "calc_goal", "calc_past",
           "market", "yearly_returns", "drawdowns", "overview", "market_list", "methodik", "unit_info", "UNIT_LABEL"]
