"""Musteranalyse – nur Dinge, die in der Forschung Bestand haben:

* Schwankung ist vorhersagbar (Volatilitäts-Cluster) → Realisierte Vola + GARCH(1,1)-Prognose
* Trend/Momentum (v. a. Krypto belegt) → Renditen 30/90/365 Tage + Lage zum 200-Tage-Schnitt
* Marktphasen → Markov-Regime-Modell auf Wochenrenditen (3 Phasen)
* Absturz vom Hoch, Zusammenhang zwischen Assets, ungewöhnliche Bewegungen im Stundenbereich
* RSI nur als Anzeige (klassischer Indikator, keine belastbare Vorhersagekraft)
"""
from __future__ import annotations

import logging
import math
import time
import warnings

import numpy as np
import pandas as pd

from . import db
from .config import ASSETS

log = logging.getLogger("invest.analysis")


def _f(x):
    """numpy → float, NaN → None (für JSON)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def periods_per_year(idx: pd.DatetimeIndex) -> float:
    span = max((idx[-1] - idx[0]).days, 1)
    return len(idx) / span * 365.25


def change_over(close: pd.Series, delta: pd.Timedelta):
    past = close.asof(close.index[-1] - delta)
    return _f(close.iloc[-1] / past - 1) if past and past == past else None


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


# ---------- Bausteine ----------
def trend_block(close: pd.Series) -> dict:
    ch = {d: change_over(close, pd.Timedelta(days=d)) for d in (7, 30, 90, 365)}
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    above = bool(close.iloc[-1] > ma200)
    score = sum(int(np.sign(ch[d])) for d in (30, 90, 365) if ch[d] is not None) + (1 if above else -1)
    label = ("stark aufwärts" if score >= 3 else "aufwärts" if score >= 1 else "seitwärts" if score == 0
             else "abwärts" if score >= -2 else "stark abwärts")
    return {"score": score, "label": label, "ma50": _f(ma50), "ma200": _f(ma200), "above_ma200": above,
            "rsi14": _f(rsi(close).iloc[-1]), "change": {f"{d}d": ch[d] for d in ch}}


def vol_block(close: pd.Series) -> dict:
    lr = np.log(close).diff().dropna()
    ppy = periods_per_year(lr.index)
    roll = lr.rolling("30D").std() * math.sqrt(ppy)
    roll = roll[roll.index >= roll.index[0] + pd.Timedelta(days=30)]
    cur = roll.iloc[-1]
    pct = float((roll < cur).mean())
    label = "ruhig" if pct < 0.33 else "normal" if pct < 0.8 else "wild"
    out = {"vol30": _f(cur), "percentile": _f(pct), "label": label, "median_vol": _f(roll.median()),
           "periods_per_year": _f(ppy)}
    try:
        from arch import arch_model

        r = lr.iloc[-3000:] * 100
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = arch_model(r, mean="Constant", vol="GARCH", p=1, q=1, dist="t").fit(disp="off", show_warning=False)
        var = res.forecast(horizon=30, reindex=False).variance.values[-1]
        out.update({
            "garch_tomorrow": _f(math.sqrt(var[0] * ppy) / 100),
            "garch_next30": _f(math.sqrt(var.mean() * ppy) / 100),
            "garch_persistence": _f(res.params.get("alpha[1]", np.nan) + res.params.get("beta[1]", np.nan)),
        })
    except Exception as e:  # GARCH ist ein Extra
        log.warning("GARCH fehlgeschlagen: %s", e)
    return out


def regime_block(close: pd.Series, k: int = 3) -> dict:
    """Markov-Regime auf Wochenrenditen. Aktuelle Phase aus gefilterten (nicht geglätteten) Wahrscheinlichkeiten."""
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

    w = (np.log(close.resample("W-SUN").last()).diff().dropna() * 100).astype(float)
    w = w[np.isfinite(w)]
    last_err = None
    for kk in (k, 2):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = MarkovRegression(w, k_regimes=kk, trend="c", switching_variance=True).fit(
                    search_reps=8, disp=False)
            break
        except Exception as e:
            last_err = e
    else:
        raise RuntimeError(f"Regime-Modell: {last_err}")
    means = [float(res.params[f"const[{i}]"]) for i in range(kk)]
    sig2 = [float(res.params[f"sigma2[{i}]"]) for i in range(kk)]
    # Benannt nach Schwankung – die trennt das Modell zuverlässig. Die Phasen-Mittelwerte sind zu verrauscht,
    # um daraus „steigend/fallend“ abzuleiten (bei Bitcoin sind z. B. alle drei positiv). Richtung kommt vom Trend.
    vol_rank = {i: r for r, i in enumerate(np.argsort(sig2))}
    vol_names = ["ruhig", "normal", "wild"] if kk == 3 else ["ruhig", "wild"]
    labels = [vol_names[vol_rank[i]] for i in range(kk)]
    filt = np.asarray(res.filtered_marginal_probabilities)
    smooth = np.asarray(res.smoothed_marginal_probabilities)
    cur = int(filt[-1].argmax())
    seq = smooth.argmax(axis=1)
    seq[-1] = cur
    segments, start = [], 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[start]:
            segments.append([w.index[start].strftime("%Y-%m-%d"), w.index[i - 1].strftime("%Y-%m-%d"),
                             labels[seq[start]]])
            start = i
    since = segments[-1][0]
    durations = np.asarray(res.expected_durations).ravel()
    return {
        "k": kk,
        "current": labels[cur],
        "prob": _f(filt[-1, cur]),
        "since": since,
        "regimes": [{"label": labels[i], "mean_pa": _f(means[i] * 52 / 100), "vol_pa": _f(math.sqrt(sig2[i] * 52) / 100),
                     "expected_weeks": _f(durations[i])} for i in range(kk)],
        "segments": segments,
    }


def drawdown_block(close: pd.Series) -> dict:
    ath = close.cummax()
    dd = close / ath - 1
    i_min = dd.idxmin()
    peak = close[:i_min].idxmax()
    return {"current": _f(dd.iloc[-1]), "ath": _f(ath.iloc[-1]), "ath_date": close.idxmax().strftime("%Y-%m-%d"),
            "max_dd": _f(dd.min()), "max_dd_date": i_min.strftime("%Y-%m-%d"), "max_dd_peak": peak.strftime("%Y-%m-%d")}


def intraday_block(df5: pd.DataFrame, now: float) -> dict:
    if df5.empty:
        return {}
    c = df5["close"]
    last_ts = int(df5["ts"].iloc[-1])
    out = {"last": _f(c.iloc[-1]), "last_ts": last_ts, "src": df5["src"].iloc[-1],
           "fresh": bool(now - last_ts < 20 * 60)}
    out["r1h"] = change_over(c, pd.Timedelta(hours=1))
    out["r24h"] = change_over(c, pd.Timedelta(hours=24))
    hourly = c.resample("1h").last().dropna()
    hr = np.log(hourly).diff().dropna()
    hist = hr[hr.index < hr.index[-1] - pd.Timedelta(hours=24)]
    if len(hist) > 100 and out["r1h"] is not None:
        sd = float(hist.std())
        out["std1h"] = _f(sd)
        out["z1h"] = _f(math.log1p(out["r1h"]) / sd) if sd > 0 else None
    return out


def cross_block(closes: dict[str, pd.Series]) -> dict:
    """Korrelation täglicher Renditen auf gemeinsamen Handelstagen (90 Tage, 1 Jahr, 5 Jahre)."""
    df = pd.DataFrame(closes).dropna()
    lr = np.log(df).diff().dropna()
    out = {}
    for name, days in (("90d", 90), ("1y", 365), ("5y", 1826)):
        sub = lr[lr.index >= lr.index[-1] - pd.Timedelta(days=days)]
        out[name] = {a: {b: _f(v) for b, v in row.items()} for a, row in sub.corr().iterrows()}
    out["common_since"] = df.index[0].strftime("%Y-%m-%d")
    return out


# ---------- Gesamtlauf ----------
def analyze_asset(con, asset: str, now: float) -> dict:
    daily = db.load_daily(con, asset)
    close = daily["close"].dropna()
    df5 = db.load_5m(con, asset, since=int(now - 120 * 86400))
    res = {"asset": asset, "name": ASSETS[asset]["name"], "updated": int(now),
           "daily_close": _f(close.iloc[-1]), "daily_day": close.index[-1].strftime("%Y-%m-%d"),
           "history_since": close.index[0].strftime("%Y-%m-%d")}
    res["trend"] = trend_block(close)
    res["vol"] = vol_block(close)
    res["drawdown"] = drawdown_block(close)
    res["intraday"] = intraday_block(df5, now)
    # Referenz fürs Allzeithoch: aus derselben Quelle wie der Live-Kurs, wenn sie lang genug zurückreicht
    first = con.execute("SELECT MIN(ts) FROM candles_5m WHERE asset=?", (asset,)).fetchone()[0]
    if first and now - first > 3 * 365 * 86400:
        res["ath_ref"] = {"value": _f(db.load_5m(con, asset)["high"].max()), "src": "5m"}
    else:
        res["ath_ref"] = {"value": _f(daily["high"].max()), "src": "1d"}
    # Regime nur alle 12 h neu schätzen (Wochendaten ändern sich langsam)
    cached = db.kv_get(con, f"regime:{asset}")
    cached_ts = db.kv_time(con, f"regime:{asset}") or 0
    if cached and now - cached_ts < 12 * 3600:
        res["regime"] = cached
    else:
        try:
            res["regime"] = regime_block(close)
            db.kv_set(con, f"regime:{asset}", res["regime"])
        except Exception as e:
            log.warning("%s Regime: %s", asset, e)
            res["regime"] = cached
    spot_df = db.load_spot(con, asset, since=int(now - 86400))
    if not spot_df.empty:
        res["spot"] = {"price": _f(spot_df["price_eur"].iloc[-1]), "ts": int(spot_df["ts"].iloc[-1])}
    return res


def run_all() -> list[str]:
    now = time.time()
    con = db.connect()
    msgs, closes, ok = [], {}, True
    for a in ASSETS:
        t0 = time.time()
        if not con.execute("SELECT 1 FROM candles_1d WHERE asset=? LIMIT 1", (a,)).fetchone():
            continue  # neuer Markt ohne Daten
        try:
            prev = db.kv_get(con, f"analysis:{a}")
            res = analyze_asset(con, a, now)
            # Marktphase = Richtung (Trend) · Schwankungsphase (Markov-Regime)
            score = res["trend"]["score"]
            direction = "Aufwärts" if score >= 1 else "Abwärts" if score <= -1 else "Seitwärts"
            res["phase"] = f"{direction} · {(res.get('regime') or {}).get('current', '–')}"
            res["direction"] = direction
            # Wechsel merken (für Telegram-Alarm)
            res["regime_change"] = (prev or {}).get("regime_change")
            res["trend_change"] = (prev or {}).get("trend_change")
            if prev and prev.get("regime") and res.get("regime") and \
                    prev["regime"]["current"] != res["regime"]["current"]:
                res["regime_change"] = {"from": prev["regime"]["current"], "to": res["regime"]["current"],
                                        "ts": int(now)}
            if prev and prev.get("direction") and prev["direction"] != direction:
                res["trend_change"] = {"from": prev["direction"], "to": direction, "ts": int(now)}
            db.kv_set(con, f"analysis:{a}", res)
            con.commit()
            closes[a] = db.load_daily(con, a)["close"]
            msgs.append(f"Analyse {a}: Trend {res['trend']['label']}, Schwankung {res['vol']['label']}, "
                        f"Phase {res['phase']} ({time.time() - t0:.1f}s)")
        except Exception as e:
            ok = False
            log.exception("Analyse %s", a)
            msgs.append(f"Analyse {a}: FEHLER {e}")
    try:
        db.kv_set(con, "analysis:_cross", cross_block(closes))
    except Exception as e:
        msgs.append(f"Korrelation: FEHLER {e}")
        ok = False
    db.log_run(con, "analyze", ok, " | ".join(msgs))
    con.commit()
    con.close()
    return msgs
