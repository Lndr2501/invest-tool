"""Prognose als Spanne (keine Punktprognose).

* bootstrap_paths: würfelt die Zukunft aus echten 20-Tage-Blöcken der Vergangenheit zusammen –
  für alle Assets mit denselben Tagen, damit der Zusammenhang (Korrelation) erhalten bleibt.
* garch_paths: wie oben, aber die Schwankung startet beim heutigen Niveau (GARCH(1,1), gefilterte
  historische Simulation). Sinnvoll für kurze Zeiträume.
* drift: 1.0 = historischer Durchschnittstrend, 0.5 = halbiert, 0 = kein Trend (nur Schwankung).

Alle Pfade sind Preis-Verhältnisse auf Kalendertagen, Start = 1.0.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

QS = (5, 25, 50, 75, 95)


def calendar_closes(daily: dict[str, pd.Series], start=None) -> pd.DataFrame:
    """Tagesschlusskurse auf Kalendertage gebracht (Wochenende = letzter Kurs)."""
    df = pd.DataFrame(daily).sort_index()
    first = max(s.first_valid_index() for s in daily.values())
    last = max(s.last_valid_index() for s in daily.values())
    df = df.reindex(pd.date_range(first, last, freq="D")).ffill()
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    return df.dropna()


def log_returns(closes: pd.DataFrame) -> np.ndarray:
    return np.diff(np.log(closes.values), axis=0)


def bootstrap_paths(R: np.ndarray, horizon: int, n_sims: int, block: int = 20, drift: float = 1.0,
                    seed: int | None = None) -> np.ndarray:
    """R: (T, A) tägliche Log-Renditen → (A, n_sims, horizon+1) Preis-Verhältnisse."""
    rng = np.random.default_rng(seed)
    T, A = R.shape
    Radj = (R - (1.0 - drift) * R.mean(axis=0)).astype(np.float32)
    L = max(1, min(block, T))
    nb = -(-horizon // L)
    starts = rng.integers(0, T - L + 1, size=(n_sims, nb))
    idx = (starts[:, :, None] + np.arange(L)[None, None, :]).reshape(n_sims, nb * L)[:, :horizon]
    out = np.empty((A, n_sims, horizon + 1), np.float32)
    out[:, :, 0] = 1.0
    for a in range(A):
        out[a, :, 1:] = np.exp(np.cumsum(Radj[idx, a], axis=1))
    return out


def garch_paths(close: pd.Series, horizon: int, n_sims: int, drift: float = 1.0, seed: int | None = None) -> np.ndarray:
    """Ein Asset, Schwankung ab heutigem Niveau. close = Handelstag-Schlusskurse. → (n_sims, horizon+1)."""
    from arch import arch_model

    lr = np.log(close).diff().dropna()
    ppy = len(lr) / max((lr.index[-1] - lr.index[0]).days, 1) * 365.25
    steps = max(1, int(round(horizon * ppy / 365.25)))
    r = lr.iloc[-3000:] * 100
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = arch_model(r, mean="Constant", vol="GARCH", p=1, q=1, dist="t").fit(disp="off", show_warning=False)
        f = res.forecast(horizon=steps, method="bootstrap", simulations=n_sims, reindex=False,
                         random_state=np.random.RandomState(seed))
    sims = f.simulations.values[-1]  # (n_sims, steps) in %
    sims = sims - res.params["mu"] + drift * lr.mean() * 100
    rel = np.concatenate([np.ones((n_sims, 1)), np.exp(np.cumsum(sims / 100, axis=1))], axis=1)
    pos = np.round(np.arange(horizon + 1) * steps / horizon).astype(int)
    return rel[:, pos].astype(np.float32)


def historical_windows(closes: pd.DataFrame, horizon: int, step: int = 7) -> tuple[np.ndarray, list[pd.Timestamp]]:
    """Alle echten Zeitfenster der Vergangenheit (Start alle `step` Tage) → (A, n, horizon+1), Startdaten."""
    V = closes.values
    T = len(V)
    if T <= horizon:
        raise ValueError(f"Zu wenig Historie für {horizon} Tage (nur {T}).")
    starts = np.arange(0, T - horizon, step)
    rel = V[starts[:, None] + np.arange(horizon + 1)] / V[starts][:, None, :]
    return np.transpose(rel, (2, 0, 1)).astype(np.float32), [closes.index[s] for s in starts]


def stress_path(closes: pd.DataFrame, lead: str, horizon: int) -> tuple[np.ndarray, pd.Timestamp, int]:
    """Start am Gipfel vor dem größten Crash von `lead`; alle Assets mit denselben Kalendertagen."""
    c = closes[lead]
    dd = c / c.cummax() - 1
    trough = dd.idxmin()
    peak = c[:trough].idxmax()
    i0 = closes.index.get_loc(peak)
    h = min(horizon, len(closes) - 1 - i0)
    V = closes.values[i0:i0 + h + 1]
    rel = (V / V[0]).T[:, None, :].astype(np.float32)  # (A, 1, h+1)
    return rel, peak, h


def custom_path(phases: list[tuple[float, int]], horizon: int, n_assets: int) -> np.ndarray:
    """Eigenes Szenario: [(Veränderung in %, Dauer in Monaten), …], danach seitwärts. → (A, 1, horizon+1)"""
    logp = np.zeros(horizon + 1)
    day, level = 0, 0.0
    for pct, months in phases:
        d = max(1, int(round(months * 30.4375)))
        target = level + math.log1p(pct / 100)
        end = min(day + d, horizon)
        if end > day:
            seg = np.linspace(level, target, d + 1)[1:end - day + 1]
            logp[day + 1:end + 1] = seg
        day, level = day + d, target
        if day >= horizon:
            break
    if day < horizon:
        logp[day + 1:] = level
    return np.repeat(np.exp(logp)[None, None, :], n_assets, axis=0).astype(np.float32)


def sims_for(horizon: int, n_assets: int, budget: float = 3e7, cap: int = 5000) -> int:
    """Anzahl Simulationen so wählen, dass der Speicher im Rahmen bleibt."""
    return int(max(500, min(cap, budget / max(1, horizon * n_assets))))


def summarize(paths: np.ndarray, amount: float = 1.0, qs=QS, points: int = 300) -> dict:
    """paths (n, H+1) Preis-Verhältnisse → Perzentile in € (× amount) über die Zeit und am Ende."""
    H = paths.shape[1] - 1
    days = np.unique(np.linspace(0, H, min(H + 1, points)).astype(int))
    pct = np.percentile(paths[:, days], qs, axis=0) * amount
    end = paths[:, -1]
    return {
        "days": days.tolist(),
        "fan": {q: pct[i].tolist() for i, q in enumerate(qs)},
        "end": {q: float(np.percentile(end, q)) * amount for q in qs},
        "p_loss": float((end < 1).mean()),
        "p_double": float((end >= 2).mean()),
        "p_minus50": float((end <= 0.5).mean()),
        "n": int(paths.shape[0]),
    }
