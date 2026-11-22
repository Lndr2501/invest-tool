"""Rechner: spielt einen Anlageplan auf vielen Preis-Pfaden durch (simuliert oder historisch).

Ablauf pro Monat: Verwahrgebühr → Einzahlung → Entnahme → Rebalancing. Am Ende: Verkauf und Steuer.
Steuer „de“ ist bewusst vereinfacht:
  * Bitcoin, Gold, Silber (§ 23 EStG): Gewinne aus Käufen, die ≤ 1 Jahr gehalten wurden, mit
    persönlichem Satz; Freigrenze 1.000 € (darunter steuerfrei, darüber alles); länger gehalten = steuerfrei.
  * Aktien, Fonds, ETFs: 26,375 % Abgeltungsteuer + Soli nach Sparerpauschbetrag (1.000 €, verheiratet 2.000 €);
    Aktienfonds/-ETFs nur auf 70 % des Gewinns (Teilfreistellung), Anleihen-ETFs und Aktien voll.
  * Nicht berücksichtigt: Steuer auf Verkäufe beim Rebalancing/Entnehmen, Vorabpauschale, Kirchensteuer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import markets
from .config import ASSETS, settings

QS = (5, 25, 50, 75, 95)
MONTH = 30.4375


@dataclass
class Costs:
    buy_fee: float = 0.0
    spread: float = 0.0
    custody_pa: float = 0.0


def default_costs() -> dict[str, Costs]:
    """Kosten je Markt: Voreinstellung der Art (markets.KINDS), überschrieben durch settings.toml [costs]."""
    over = settings()["costs"]
    return {a: Costs(**{**markets.costs_for(a), **over.get(a, {})}) for a in ASSETS}


# Steuerpflichtiger Anteil eines Gewinns bei Abgeltungsteuer (Teilfreistellung)
TAXABLE_SHARE = {"fund_equity": 0.7, "fund_mixed": 0.85, "fund_other": 1.0, "stock": 1.0}


@dataclass
class Plan:
    weights: dict[str, float]
    horizon_days: int = 365
    initial: float = 100.0
    monthly: float = 0.0
    contrib_months: int | None = None  # None = über die ganze Dauer
    withdraw_monthly: float = 0.0
    withdraw_from_month: int | None = None  # None = keine Entnahme
    rebalance: str = "none"  # none | quarterly | yearly
    costs: dict[str, Costs] = field(default_factory=default_costs)
    tax: str = "none"  # none | de | flat
    personal_rate: float = 0.35
    flat_rate: float = 0.25
    allowance: float = 1000.0  # Sparerpauschbetrag (verheiratet 2.000 €)
    inflation_pa: float = 0.02
    goal: float | None = None
    # Vorhandener Bestand: Asset → (heutiger Wert, Einstandswert, Tage seit Kauf). Ohne Kaufgebühr.
    start: dict[str, tuple[float, float, int]] = field(default_factory=dict)


def _pct(x: np.ndarray) -> dict:
    return {q: float(np.percentile(x, q)) for q in QS}


def break_even(plan: Plan, assets: list[str], w: np.ndarray) -> float:
    """Um wie viel % müssen alle Kurse steigen, damit eine Einmalanlage nach Kosten bei null landet?"""
    months = plan.horizon_days / MONTH
    keep = 0.0
    for a, wa in zip(assets, w):
        c = plan.costs.get(a, Costs())
        keep += wa * (1 - c.buy_fee) ** 2 * (1 - c.spread / 2) / (1 + c.spread / 2) * (1 - c.custody_pa / 12) ** months
    return 1 / keep - 1


def simulate(plan: Plan, paths: dict[str, np.ndarray], points: int = 300, extra_days=None) -> dict:
    """paths: Asset → (n, H+1) Preis-Verhältnisse (gleiche n und H für alle Assets)."""
    assets = [a for a, v in plan.weights.items() if v > 0]
    assets += [a for a, v in plan.start.items() if v[0] > 0 and a not in assets]
    w = np.array([plan.weights.get(a, 0.0) for a in assets], float)
    w = w / w.sum() if w.sum() > 0 else np.full(len(assets), 1.0 / max(len(assets), 1))
    start_value = float(sum(v[0] for v in plan.start.values() if v[0] > 0))
    P = np.stack([paths[a] for a in assets]).astype(np.float64)  # (A, n, H+1)
    A, n, H1 = P.shape
    H = H1 - 1
    fee = np.array([plan.costs.get(a, Costs()).buy_fee for a in assets])
    spr = np.array([plan.costs.get(a, Costs()).spread for a in assets])
    cust = np.array([plan.costs.get(a, Costs()).custody_pa for a in assets])
    net_sell = (1 - spr / 2) * (1 - fee)  # Erlös je Euro Mittelkurs beim Verkauf
    buy_mult = (1 - fee) / (1 + spr / 2)  # Einheiten-Euro je gezahltem Euro

    ev_days = np.unique(np.round(np.arange(0, H / MONTH + 1e-9) * MONTH).astype(int))
    ev_days = ev_days[ev_days < H] if H > 0 else np.array([0])
    M = len(ev_days)
    reb_step = {"quarterly": 3, "yearly": 12}.get(plan.rebalance)

    units = np.zeros((A, n))
    C = np.ones((A, n))  # Restfaktor durch Verkäufe/Gebühren (für Steuer-Lose)
    lots: list[list[tuple[int, np.ndarray, np.ndarray, np.ndarray]]] = [[] for _ in range(A)]
    U_hist = np.zeros((A, n, M))
    inv_hist = np.zeros(M)
    wd_hist = np.zeros((n, M))
    invested = 0.0
    withdrawn = np.zeros(n)
    ran_out = np.full(n, -1)

    for m, d in enumerate(ev_days):
        price = P[:, :, d]
        if m > 0 and cust.any():
            f = (1 - cust / 12)[:, None]
            units *= f
            C *= f
        if m == 0 and start_value > 0:
            for i, a in enumerate(assets):
                val, basis, held = plan.start.get(a, (0.0, 0.0, 0))
                if val > 0:
                    u = val / price[i]
                    units[i] += u
                    lots[i].append((-int(held), u, np.full(n, float(basis)), C[i].copy()))
            invested += start_value
        amt = (plan.initial if m == 0 else 0.0) + (
            plan.monthly if (plan.contrib_months is None or m < plan.contrib_months) else 0.0)
        if amt > 0:
            for i in range(A):
                pay = amt * w[i]
                u = pay * buy_mult[i] / price[i]
                units[i] += u
                lots[i].append((int(d), u, np.full(n, pay), C[i].copy()))
            invested += amt
        if plan.withdraw_from_month is not None and m >= plan.withdraw_from_month and plan.withdraw_monthly > 0:
            val = (units * price * net_sell[:, None]).sum(0)
            take = np.minimum(plan.withdraw_monthly, val)
            frac = np.divide(take, val, out=np.zeros(n), where=val > 0)
            units *= 1 - frac
            C *= 1 - frac
            withdrawn += take
            short = (take < plan.withdraw_monthly - 1e-9) & (ran_out < 0)
            ran_out[short] = m
        if reb_step and A > 1 and m > 0 and m % reb_step == 0:
            mid = units * price
            V = mid.sum(0)
            target = w[:, None] * V
            excess = np.maximum(mid - target, 0)
            need = np.maximum(target - mid, 0)
            sell = np.divide(excess, mid, out=np.zeros_like(mid), where=mid > 0)
            cash = (excess * net_sell[:, None]).sum(0)
            units *= 1 - sell
            C *= 1 - sell
            need_tot = need.sum(0)
            buy = need * np.divide(cash, need_tot, out=np.zeros(n), where=need_tot > 0)
            for i in range(A):
                u = buy[i] * buy_mult[i] / price[i]
                units[i] += u
                lots[i].append((int(d), u, buy[i].copy(), C[i].copy()))
        U_hist[:, :, m] = units
        inv_hist[m] = invested
        wd_hist[:, m] = withdrawn

    # Depotwert über die Zeit
    j = np.searchsorted(ev_days, np.arange(H + 1), side="right") - 1
    value = np.zeros((n, H + 1))
    for i in range(A):
        value += U_hist[i][:, j] * P[i]
    inv_t = inv_hist[j]
    wd_t = wd_hist[:, j]

    # Verkauf am Ende + Steuer
    P_T = P[:, :, -1]
    final_mid = (units * P_T).sum(0)
    payout = (units * P_T * net_sell[:, None]).sum(0)
    tax = np.zeros(n)
    gain_short_private = np.zeros(n)
    gain_fund = np.zeros(n)
    gain_all = np.zeros(n)
    for i, a in enumerate(assets):
        kind = ASSETS.get(a, {}).get("tax", "private")
        for d, u, cost, c_buy in lots[i]:
            ratio = np.divide(C[i], c_buy, out=np.zeros(n), where=c_buy > 0)
            g = u * ratio * P_T[i] * net_sell[i] - cost * ratio
            gain_all += g
            if kind in TAXABLE_SHARE:
                gain_fund += g * TAXABLE_SHARE[kind]
            elif H - d <= 365:
                gain_short_private += g
    if plan.tax == "flat":
        tax = np.maximum(gain_all, 0) * plan.flat_rate
    elif plan.tax == "de":
        tax += np.where(gain_short_private >= 1000, gain_short_private * plan.personal_rate, 0.0)
        tax += np.maximum(gain_fund - plan.allowance, 0) * 0.26375
    after_tax = payout - tax
    deflator = (1 + plan.inflation_pa) ** (H / 365.25)
    profit = after_tax + withdrawn - invested

    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(inv_t > 0, (value + wd_t - inv_t) / inv_t, 0.0)
        peak = np.maximum.accumulate(value, axis=1)
        dd = np.where(peak > 0, value / peak - 1, 0.0)
    worst = rel.min(axis=1)
    max_dd = dd.min(axis=1)

    days = np.linspace(0, H, min(H + 1, points)).astype(int)
    if extra_days is not None:
        days = np.concatenate([days, np.clip(np.asarray(extra_days, int), 0, H)])
    days = np.unique(days)
    fan = np.percentile(value[:, days], QS, axis=0)
    res = {
        "n": int(n), "horizon_days": int(H), "assets": assets, "weights": w.tolist(),
        "invested": float(invested),
        "start_value": start_value,
        "withdrawn": _pct(withdrawn),
        "final_value": _pct(final_mid),
        "payout": _pct(payout),
        "tax": _pct(tax),
        "after_tax": _pct(after_tax),
        "real_after_tax": _pct(after_tax / deflator),
        "profit": _pct(profit),
        "p_loss": float((profit < 0).mean()),
        "p_goal": float((after_tax >= plan.goal).mean()) if plan.goal else None,
        "worst_moment": _pct(worst),
        "max_dd": _pct(max_dd),
        "ran_out_share": float((ran_out >= 0).mean()) if plan.withdraw_from_month is not None else None,
        "ran_out_month_median": float(np.median(ran_out[ran_out >= 0])) if (ran_out >= 0).any() else None,
        "break_even_pct": float(break_even(plan, assets, w)),
        "deflator": float(deflator),
        "fan": {"days": days.tolist(), "pct": {q: fan[k].tolist() for k, q in enumerate(QS)},
                "invested": inv_t[days].tolist()},
        "per_sim": {"after_tax": after_tax, "payout": payout, "profit": profit, "worst": worst,
                    "value": value[:, days]},
    }
    return res


def inflation_stats(hicp: pd.Series) -> dict:
    """Durchschnittliche Inflation p. a. aus dem HVPI-Index."""
    out = {}
    if hicp.empty:
        return out
    last = hicp.index[-1]
    for years in (1, 5, 10, 20):
        past = last - 12 * years
        if past in hicp.index:
            out[f"{years}y"] = float((hicp[last] / hicp[past]) ** (1 / years) - 1)
    out["last_month"] = str(last)
    return out


def deflate_path(hicp: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Tatsächliche Preissteigerung zwischen zwei Daten (für historische Rechnungen)."""
    s = hicp.copy()
    s.index = s.index.to_timestamp()
    a = s.asof(start)
    b = s.asof(end)
    return float(b / a) if a and b else 1.0
