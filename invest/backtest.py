"""Training & ehrlicher Selbsttest (Walk-Forward: Modell sieht nur die Vergangenheit, nie die Zukunft).

A) Kalibrierung der Prognose-Spanne: Liegt der echte Kurs in ~90 % der Fälle im 5–95-%-Band?
   Getestet für 30/90/365 Tage und drei Trend-Annahmen (voll, halb, kein Trend) + GARCH für 30 Tage.
B) Richtung: Schlagen Regeln/ML-Modelle die simple Basis „steigt immer“ – und nach Gebühren Buy & Hold?
C) Minuten-Muster: Lässt sich aus der letzten Stunde die nächste Stunde ableiten – nach Kosten?
"""
from __future__ import annotations

import logging
import math
import time
import warnings

import numpy as np
import pandas as pd

from . import db
from . import markets
from .config import ASSETS, settings
from .forecast import bootstrap_paths, calendar_closes, garch_paths

log = logging.getLogger("invest.backtest")


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


# ---------- A) Kalibrierung ----------
def calibration(close_cal: pd.Series, close_trading: pd.Series, horizons=(30, 90, 365), drifts=(1.0, 0.5, 0.0),
                step: int = 30, min_hist: int = 3 * 365, n: int = 1000, garch: bool = True) -> list[dict]:
    V = close_cal.values
    R = np.diff(np.log(V))
    out = []
    for H in horizons:
        dates = list(range(min_hist, len(V) - H, step))
        if len(dates) < 10:
            continue
        configs = [("bootstrap", d) for d in drifts]
        if garch and H == 30:
            configs += [("garch", 1.0), ("garch", 0.0)]
        for method, drift in configs:
            rows = []
            for k, i in enumerate(dates):
                if method == "bootstrap":
                    p = bootstrap_paths(R[:i, None], H, n, drift=drift, seed=k)[0, :, -1]
                else:
                    if k % 3:  # GARCH nur jedes 3. Datum (Rechenzeit)
                        continue
                    hist = close_trading[close_trading.index <= close_cal.index[i]]
                    p = garch_paths(hist, H, n, drift=drift, seed=k)[:, -1]
                q = np.percentile(p, (5, 25, 50, 75, 95))
                actual = V[i + H] / V[i]
                rows.append((close_cal.index[i], *q, actual))
            df = pd.DataFrame(rows, columns=["date", "p5", "p25", "p50", "p75", "p95", "actual"])
            a = df["actual"]
            mae_model = np.abs(np.log(a / df["p50"])).mean()
            mae_rw = np.abs(np.log(a)).mean()
            out.append({
                "horizon": H, "method": method, "drift": drift, "n_dates": len(df),
                "cov90": _f(((a >= df.p5) & (a <= df.p95)).mean()),
                "cov50": _f(((a >= df.p25) & (a <= df.p75)).mean()),
                "below5": _f((a < df.p5).mean()), "above95": _f((a > df.p95).mean()),
                "skill_vs_rw": _f(1 - mae_model / mae_rw) if mae_rw > 0 else None,
                "series": {"date": df["date"].dt.strftime("%Y-%m-%d").tolist(),
                           **{c: df[c].round(5).tolist() for c in ("p5", "p50", "p95", "actual")}},
            })
    return out


def best_drift(cal: list[dict]) -> dict:
    """Welche Trend-Annahme traf das 90-%-Band am besten (Abweichung von 0.90, gemittelt über Zeiträume)?"""
    score: dict[float, list[float]] = {}
    for c in cal:
        if c["method"] == "bootstrap" and c["cov90"] is not None:
            score.setdefault(c["drift"], []).append(abs(c["cov90"] - 0.9))
    if not score:
        return {}
    best = min(score, key=lambda d: np.mean(score[d]))
    return {"drift": best, "mean_abs_error": _f(np.mean(score[best])),
            "all": {str(d): _f(np.mean(v)) for d, v in score.items()}}


# ---------- B) Richtung ----------
def features(close: pd.Series) -> pd.DataFrame:
    lr = np.log(close).diff()
    f = pd.DataFrame(index=close.index)
    for n in (1, 5, 21, 63, 252):
        f[f"r{n}"] = np.log(close / close.shift(n))
    f["vol21"] = lr.rolling(21).std()
    f["vol63"] = lr.rolling(63).std()
    f["dd"] = close / close.cummax() - 1
    f["ma50"] = close / close.rolling(50).mean() - 1
    f["ma200"] = close / close.rolling(200).mean() - 1
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    f["rsi"] = 100 - 100 / (1 + up / dn)
    return f


def _strategy(pos: np.ndarray, r_next: np.ndarray, cost: float, ppy: float) -> dict:
    trades = np.abs(np.diff(np.concatenate([[0], pos])))
    ret = pos * r_next + np.log1p(-trades * cost)  # Log-Renditen, Kosten je Wechsel
    eq = np.exp(np.cumsum(ret))
    years = len(ret) / ppy
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    return {"multiple": _f(eq[-1]), "cagr": _f(eq[-1] ** (1 / years) - 1) if years > 0 else None,
            "max_dd": _f((eq / peak - 1).min()), "sharpe": _f(ret.mean() / sd * math.sqrt(ppy)) if sd > 0 else None,
            "trades": int(trades.sum())}


def direction(close: pd.Series, horizon_days: int, cost: float, min_train: int = 500, retrain: int = 63) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    close = close.dropna()
    ppy = len(close) / max((close.index[-1] - close.index[0]).days, 1) * 365.25
    h = max(1, int(round(horizon_days * ppy / 365.25)))
    X = features(close)
    fwd = np.log(close.shift(-h) / close)
    y = (fwd > 0).astype(float)
    r_next = np.log(close.shift(-1) / close)  # Rendite des nächsten Tages (für die Strategie)
    ok = X.notna().all(axis=1) & fwd.notna() & r_next.notna()
    X, y, r_next = X[ok], y[ok], r_next[ok]
    cols = list(X.columns)
    preds = {k: [] for k in ("Immer steigt", "Momentum 3 Monate", "Über 200-Tage-Linie", "Logistische Regression",
                             "Gradient Boosting")}
    idx_test = []
    for s in range(min_train, len(X), retrain):
        e = min(s + retrain, len(X))
        tr = slice(0, max(0, s - h))  # Lücke von h Tagen gegen Datenleck (überlappende Ziele)
        Xtr, ytr = X.iloc[tr][cols].values, y.iloc[tr].values
        Xte = X.iloc[s:e][cols].values
        idx_test.extend(range(s, e))
        preds["Immer steigt"].extend([1.0] * (e - s))
        preds["Momentum 3 Monate"].extend((X.iloc[s:e]["r63"] > 0).astype(float).tolist())
        preds["Über 200-Tage-Linie"].extend((X.iloc[s:e]["ma200"] > 0).astype(float).tolist())
        if len(np.unique(ytr)) < 2:
            preds["Logistische Regression"].extend([1.0] * (e - s))
            preds["Gradient Boosting"].extend([1.0] * (e - s))
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lr_m = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500)).fit(Xtr, ytr)
            gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150,
                                                l2_regularization=1.0, random_state=0).fit(Xtr, ytr)
        preds["Logistische Regression"].extend(lr_m.predict(Xte).tolist())
        preds["Gradient Boosting"].extend(gb.predict(Xte).tolist())
    yt = y.iloc[idx_test].values
    rt = r_next.iloc[idx_test].values
    n_eff = max(1.0, len(yt) / h)
    base = float(yt.mean())
    bh = _strategy(np.ones(len(rt)), rt, cost, ppy)
    models = []
    for name, p in preds.items():
        p = np.asarray(p)
        hit = float((p == yt).mean())
        se = math.sqrt(max(base * (1 - base), 1e-9) / n_eff)
        z = (hit - base) / se
        strat = _strategy(p, rt, cost, ppy)
        approved = name != "Immer steigt" and z > 1.96 and (strat["sharpe"] or -9) > (bh["sharpe"] or -9)
        models.append({"name": name, "hit": _f(hit), "z_vs_base": _f(z), "share_invested": _f(p.mean()),
                       "strategy": strat, "approved": bool(approved)})
    return {"horizon_days": horizon_days, "h_obs": h, "test_from": X.index[idx_test[0]].strftime("%Y-%m-%d"),
            "n_test": len(yt), "n_eff": _f(n_eff), "base_rate_up": _f(base), "buy_hold": bh, "cost": cost,
            "models": models}


# ---------- C) Minuten-Muster ----------
def intraday_pattern(df5: pd.DataFrame, cost: float) -> dict:
    if len(df5) < 5000:
        return {}
    hourly = df5["close"].resample("1h").last().dropna()
    r = np.log(hourly).diff().dropna()
    r = r[np.isfinite(r)]
    prev, nxt = r.values[:-1], r.values[1:]
    ac = float(np.corrcoef(prev, nxt)[0, 1])
    up_next = float(nxt[prev > 0].mean()) * 1e4
    dn_next = float(nxt[prev < 0].mean()) * 1e4
    same_dir = float((np.sign(prev) == np.sign(nxt))[(prev != 0) & (nxt != 0)].mean())
    return {"hours": int(len(r)), "since": r.index[0].strftime("%Y-%m-%d"), "autocorr": _f(ac),
            "next_after_up_bp": _f(up_next), "next_after_down_bp": _f(dn_next), "same_direction": _f(same_dir),
            "roundtrip_cost_bp": _f(cost * 2 * 1e4)}


def run_all() -> list[str]:
    t_all = time.time()
    con = db.connect()
    costs = settings()["costs"]
    msgs, ok = [], True
    for a in ASSETS:
        t0 = time.time()
        try:
            daily = db.load_daily(con, a)["close"].dropna()
            if len(daily) < 3 * 250:
                msgs.append(f"Backtest {a}: übersprungen (zu wenig Historie)")
                continue
            cal = calendar_closes({a: daily})[a]
            c = {**markets.costs_for(a), **costs.get(a, {})}
            one_way = c.get("buy_fee", 0) + c.get("spread", 0) / 2
            cali = calibration(cal, daily)
            res = {
                "asset": a, "updated": int(time.time()),
                "calibration": cali,
                "best_drift": best_drift(cali),
                "direction": [direction(daily, d, one_way) for d in (7, 30)],
                "intraday": intraday_pattern(db.load_5m(con, a), one_way),
            }
            db.kv_set(con, f"backtest:{a}", res)
            con.commit()
            appr = [m["name"] for d in res["direction"] for m in d["models"] if m["approved"]]
            msgs.append(f"Backtest {a}: beste Trend-Annahme {res['best_drift'].get('drift')}, "
                        f"freigegebene Richtungsmodelle: {appr or 'keine'} ({time.time() - t0:.0f}s)")
        except Exception as e:
            ok = False
            log.exception("Backtest %s", a)
            msgs.append(f"Backtest {a}: FEHLER {e}")
    db.log_run(con, "backtest", ok, " | ".join(msgs))
    con.commit()
    con.close()
    msgs.append(f"gesamt {time.time() - t_all:.0f}s")
    return msgs
