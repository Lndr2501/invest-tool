"""Invest-Tool – Oberfläche (Streamlit). Start: streamlit run app.py"""
from __future__ import annotations

import datetime as dt
import math
import re
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from invest import calculator as calc
from invest import db, forecast
from invest.config import ASSETS, settings, telegram_conf
from invest.fmt import delta, eur, num, pct

st.set_page_config(page_title="Invest-Tool", page_icon="📈", layout="wide")

NAMES = {a: v["name"] for a, v in ASSETS.items()}
PCT_NAMES = {5: "schlecht (5 %)", 25: "eher schlecht (25 %)", 50: "typisch (50 %)", 75: "eher gut (75 %)",
             95: "gut (95 %)"}
# Farben: validiert mit dataviz/validate_palette.js (hell + dunkel). Farbe folgt dem Asset, nie dem Rang.
PALETTE = {
    "light": {"BTC": "#2a78d6", "GOLD": "#eb6834", "SILVER": "#1baf7a", "MSCIW": "#eda100",
              "grid": "#e9e8e4", "text": "#0b0b0b", "text2": "#52514e", "neutral": "#8a8984",
              "band_outer": "#cde2fb", "band_inner": "#86b6ef", "median": "#1c5cab",
              "up": "rgba(42,120,214,0.09)", "wild": "rgba(227,73,72,0.16)", "normal": "rgba(227,73,72,0.06)",
              "mid": "#f0efec",
              "neg": "#e34948", "pos": "#2a78d6"},
    "dark": {"BTC": "#3987e5", "GOLD": "#d95926", "SILVER": "#199e70", "MSCIW": "#c98500",
             "grid": "#2c2c2a", "text": "#f0efec", "text2": "#c3c2b7", "neutral": "#8f8e88",
             "band_outer": "#184f95", "band_inner": "#2a78d6", "median": "#9ec5f4",
             "up": "rgba(57,135,229,0.14)", "wild": "rgba(230,103,103,0.20)", "normal": "rgba(230,103,103,0.08)",
             "mid": "#383835",
             "neg": "#e66767", "pos": "#3987e5"},
}


def C() -> dict:
    try:
        kind = st.context.theme.type or "light"
    except Exception:
        kind = "light"
    return PALETTE["dark" if kind == "dark" else "light"]


# ---------- Daten (gecacht) ----------
@st.cache_data(ttl=60, show_spinner=False)
def daily(asset: str) -> pd.DataFrame:
    con = db.connect(readonly=True)
    try:
        return db.load_daily(con, asset)
    finally:
        con.close()


@st.cache_data(ttl=60, show_spinner=False)
def intraday(asset: str, days: float) -> pd.DataFrame:
    con = db.connect(readonly=True)
    try:
        return db.load_5m(con, asset, since=int(time.time() - days * 86400))
    finally:
        con.close()


@st.cache_data(ttl=60, show_spinner=False)
def kv(key: str):
    con = db.connect(readonly=True)
    try:
        return db.kv_get(con, key)
    finally:
        con.close()


@st.cache_data(ttl=60, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = db.connect(readonly=True)
    try:
        return pd.read_sql(sql, con, params=params)
    finally:
        con.close()


@st.cache_data(ttl=3600, show_spinner=False)
def hicp() -> pd.Series:
    con = db.connect(readonly=True)
    try:
        return db.load_hicp(con, settings()["inflation"]["geo"])
    finally:
        con.close()


def closes(assets: list[str]) -> pd.DataFrame:
    return forecast.calendar_closes({a: daily(a)["close"].dropna() for a in assets})


# ---------- Grafik-Helfer ----------
def style(fig: go.Figure, height: int = 360, money: bool = False, pct_axis: bool = False) -> go.Figure:
    c = C()
    fig.update_layout(height=height, margin=dict(l=4, r=4, t=36, b=4), hovermode="x unified", separators=",.",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showgrid=False, linecolor=c["grid"], ticks="")
    fig.update_yaxes(gridcolor=c["grid"], gridwidth=1, zeroline=False, linecolor=c["grid"],
                     ticksuffix=" €" if money else "", tickformat=".0%" if pct_axis else None)
    return fig


def show(fig: go.Figure):
    st.plotly_chart(fig, use_container_width=True, theme="streamlit", config={"displaylogo": False})


def end_labels(fig: go.Figure, series: dict[str, pd.Series], fmt=lambda v: f"{v:.0f}"):
    """Direkte Beschriftung am Linienende (Text in Textfarbe, Punkt in Asset-Farbe)."""
    c = C()
    series = {a: s.dropna() for a, s in series.items() if not s.dropna().empty}
    if not series:
        return
    lo = min(s.min() for s in series.values())
    hi = max(s.max() for s in series.values())
    plot_px = 280.0  # ungefähre Plot-Höhe in Pixeln
    to_px = (lambda v: (v - lo) / (hi - lo) * plot_px) if hi > lo else (lambda v: 0.0)
    order = sorted(series, key=lambda a: series[a].iloc[-1])
    placed: dict[str, float] = {}
    last = -1e9
    for a in order:  # von unten nach oben, Mindestabstand 15 px
        px = max(to_px(series[a].iloc[-1]), last + 15)
        placed[a] = px
        last = px
    x0 = min(s.index[0] for s in series.values())
    x1 = max(s.index[-1] for s in series.values())
    for a, s in series.items():
        fig.add_trace(go.Scatter(x=[s.index[-1]], y=[s.iloc[-1]], mode="markers", cliponaxis=False,
                                 marker=dict(size=8, color=c[a]), showlegend=False, hoverinfo="skip"))
        fig.add_annotation(x=s.index[-1], y=s.iloc[-1], text=f"{NAMES[a]} {fmt(s.iloc[-1])}", showarrow=False,
                           xanchor="left", xshift=8, yshift=placed[a] - to_px(s.iloc[-1]),
                           font=dict(color=c["text2"], size=12))
    fig.update_xaxes(range=[x0, x1])


def fan_chart(x, pcts: dict, invested=None, height: int = 380, money: bool = True, title: str | None = None):
    c = C()
    fig = go.Figure()
    p = {int(k): np.asarray(v) for k, v in pcts.items()}
    fig.add_trace(go.Scatter(x=x, y=p[95], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[5], fill="tonexty", fillcolor=c["band_outer"], line=dict(width=0),
                             name="90 % der Fälle", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[75], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[25], fill="tonexty", fillcolor=c["band_inner"], line=dict(width=0),
                             name="50 % der Fälle", hoverinfo="skip"))
    unit = " €" if money else ""
    fig.add_trace(go.Scatter(x=x, y=p[50], line=dict(color=c["median"], width=2), name="typisch",
                             customdata=np.stack([p[5], p[95]], axis=1),
                             hovertemplate=f"typisch %{{y:,.0f}}{unit}<br>schlecht (5 %%) %{{customdata[0]:,.0f}}{unit}"
                                           f"<br>gut (95 %%) %{{customdata[1]:,.0f}}{unit}<extra></extra>"))
    if invested is not None:
        fig.add_trace(go.Scatter(x=x, y=invested, line=dict(color=c["neutral"], width=2), name="eingezahlt",
                                 hovertemplate=f"eingezahlt %{{y:,.0f}}{unit}<extra></extra>"))
    if title:
        st.markdown(f"**{title}**")
    return style(fig, height, money=money)


def chip(label: str, kind: str) -> str:
    icon = {"good": "🟢", "warn": "🟡", "bad": "🔴", "info": "🔵", "none": "⚪"}[kind]
    return f"{icon} {label}"


def trend_kind(label: str) -> str:
    return {"stark aufwärts": "good", "aufwärts": "good", "seitwärts": "none", "abwärts": "warn",
            "stark abwärts": "bad"}.get(label, "none")


def vol_kind(label: str) -> str:
    return {"ruhig": "good", "normal": "warn", "wild": "bad"}.get(label, "none")


def age_text(ts: int | None) -> str:
    if not ts:
        return "–"
    m = (time.time() - ts) / 60
    return f"vor {m:.0f} min" if m < 90 else f"vor {m / 60:.0f} h" if m < 48 * 60 else f"vor {m / 1440:.0f} Tagen"


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


# =====================================================================
# Seite 1: Übersicht
# =====================================================================
def page_overview():
    st.title("Übersicht")
    cols = st.columns(len(ASSETS))
    for col, a in zip(cols, ASSETS):
        ana = kv(f"analysis:{a}") or {}
        intr = ana.get("intraday") or {}
        with col.container(border=True):
            price = intr.get("last") or ana.get("daily_close")
            st.metric(NAMES[a], eur(price), delta=delta(intr.get("r24h"), suffix=" (24 h)"))
            ch = (ana.get("trend") or {}).get("change", {})
            st.caption(f"1 h {pct(intr.get('r1h'))} · 7 T {pct(ch.get('7d'))} · 30 T {pct(ch.get('30d'))} · "
                       f"1 J {pct(ch.get('365d'))}")
            if ana:
                st.markdown(f"{chip('Trend ' + ana['trend']['label'], trend_kind(ana['trend']['label']))}  \n"
                            f"{chip('Schwankung ' + ana['vol']['label'], vol_kind(ana['vol']['label']))}  \n"
                            f"{chip('Phase: ' + ana.get('phase', '–'), 'info')}  \n"
                            f"vom Allzeithoch: **{pct(ana['drawdown']['current'])}**")
            if ana.get("spot"):
                st.caption(f"Spotpreis (gold-api): {eur(ana['spot']['price'])}")
            st.caption(f"Kurs {age_text(intr.get('last_ts'))} · {intr.get('src', '')}")

    st.subheader("Vergleich: Wertentwicklung")
    rng = st.segmented_control("Zeitraum", ["1 M", "3 M", "1 J", "3 J", "5 J", "10 J"], default="1 J",
                               key="ov_range")
    days = {"1 M": 30, "3 M": 91, "1 J": 365, "3 J": 1095, "5 J": 1826, "10 J": 3652}[rng or "1 J"]
    df = closes(list(ASSETS))
    df = df[df.index >= df.index[-1] - pd.Timedelta(days=days)]
    idx = df / df.iloc[0] * 100
    c = C()
    fig = go.Figure()
    for a in ASSETS:
        fig.add_trace(go.Scatter(x=idx.index, y=idx[a], name=NAMES[a], line=dict(color=c[a], width=2),
                                 hovertemplate=f"{NAMES[a]} %{{y:.1f}}<extra></extra>"))
    end_labels(fig, {a: idx[a] for a in ASSETS}, fmt=lambda v: f"{v - 100:+.0f} %".replace("-", "−"))
    fig.add_hline(y=100, line=dict(color=c["neutral"], width=1))
    style(fig, 380)
    fig.update_layout(yaxis_title="Start = 100", margin=dict(r=130))
    show(fig)
    st.caption("Alle Kurse in Euro. Gold und Silber: Terminkontrakte (COMEX), MSCI World: iShares-ETF (Xetra).")

    st.subheader("Letzte Alarme")
    al = query("SELECT ts, asset, rule, message, sent FROM alerts WHERE rule != 'report' ORDER BY ts DESC LIMIT 5")
    if al.empty:
        st.caption("Noch keine Alarme.")
    for _, r in al.iterrows():
        when = dt.datetime.fromtimestamp(r.ts).strftime("%d.%m. %H:%M")
        st.markdown(f"**{when}** – {strip_html(r.message).splitlines()[0]}")


# =====================================================================
# Seite 2: Chart & Muster
# =====================================================================
def page_patterns():
    st.title("Chart & Muster")
    a = st.selectbox("Asset", list(ASSETS), format_func=NAMES.get, key="pat_asset")
    ana = kv(f"analysis:{a}") or {}
    rng = st.segmented_control("Zeitraum", ["1 T", "7 T", "1 M", "3 M", "1 J", "3 J", "Alles"], default="1 J",
                               key="pat_range")
    rng = rng or "1 J"
    c = C()
    fig = go.Figure()
    if rng in ("1 T", "7 T", "1 M"):
        d = {"1 T": 1, "7 T": 7, "1 M": 31}[rng]
        df = intraday(a, d)
        s = df["close"] if rng != "1 M" else df["close"].resample("1h").last().dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s, name=NAMES[a], line=dict(color=c[a], width=2),
                                 hovertemplate="%{y:,.2f} €<extra></extra>"))
        st.caption(f"{'5-Minuten' if rng != '1 M' else 'Stunden'}-Kurse (UTC), Quelle {df['src'].iloc[-1] if len(df) else '–'}")
    else:
        dd = daily(a)
        days = {"3 M": 91, "1 J": 365, "3 J": 1095, "Alles": 100000}[rng]
        full = dd["close"]
        s = full[full.index >= full.index[-1] - pd.Timedelta(days=days)]
        ma50 = full.rolling(50).mean().reindex(s.index)
        ma200 = full.rolling(200).mean().reindex(s.index)
        fig.add_trace(go.Scatter(x=s.index, y=s, name=NAMES[a], line=dict(color=c[a], width=2),
                                 hovertemplate="%{y:,.2f} €<extra></extra>"))
        fig.add_trace(go.Scatter(x=s.index, y=ma50, name="50-Tage-Schnitt", line=dict(color=c["neutral"], width=1),
                                 hovertemplate="Ø50 %{y:,.2f} €<extra></extra>"))
        fig.add_trace(go.Scatter(x=s.index, y=ma200, name="200-Tage-Schnitt", line=dict(color=c["text2"], width=2),
                                 hovertemplate="Ø200 %{y:,.2f} €<extra></extra>"))
        reg = ana.get("regime") or {}
        for start, end, label in reg.get("segments", []):
            if pd.Timestamp(end) < s.index[0] or label == "ruhig":
                continue
            fig.add_vrect(x0=max(pd.Timestamp(start) - pd.Timedelta(days=7), s.index[0]), x1=pd.Timestamp(end),
                          fillcolor=c["wild"] if label == "wild" else c["normal"], line_width=0, layer="below")
        st.caption("Hintergrund: dunkler = wildere Schwankungsphase, ohne Fläche = ruhig (Markov-Modell auf Wochenrenditen).")
    show(style(fig, 420, money=True))

    if not ana:
        st.info("Noch keine Analyse – läuft stündlich.")
        return
    t, v, r, d = ana["trend"], ana["vol"], ana.get("regime") or {}, ana["drawdown"]
    c1, c2, c3, c4 = st.columns(4)
    with c1.container(border=True):
        st.markdown(f"**Trend**  \n{chip(t['label'], trend_kind(t['label']))}")
        st.caption(f"30 T {pct(t['change']['30d'])} · 90 T {pct(t['change']['90d'])} · 1 J {pct(t['change']['365d'])}  \n"
                   f"{'über' if t['above_ma200'] else 'unter'} dem 200-Tage-Schnitt ({eur(t['ma200'])})  \n"
                   f"RSI 14: {num(t['rsi14'], 0)} (nur Anzeige)")
    with c2.container(border=True):
        st.markdown(f"**Schwankung**  \n{chip(v['label'], vol_kind(v['label']))}")
        st.caption(f"letzte 30 T: {pct(v['vol30'], 0, sign=False)} p. a.  \n"
                   f"höher als an {pct(v['percentile'], 0, sign=False)} aller Tage  \n"
                   f"GARCH-Prognose 30 T: {pct(v.get('garch_next30'), 0, sign=False)} p. a.")
    with c3.container(border=True):
        st.markdown(f"**Marktphase**  \n{chip(ana.get('phase', '–'), 'info')}")
        st.caption(f"Richtung aus dem Trend, Schwankungsphase aus dem Markov-Modell: "
                   f"„{r.get('current', '–')}“ seit {r.get('since', '–')} (Sicherheit {pct(r.get('prob'), 0, sign=False)})")
    with c4.container(border=True):
        st.markdown(f"**Vom Allzeithoch**  \n{pct(d['current'])}")
        st.caption(f"Hoch am {d['ath_date']} ({eur(d['ath'])})  \n"
                   f"größter Absturz: {pct(d['max_dd'])} ({d['max_dd_peak']} → {d['max_dd_date']})")

    with st.expander("Marktphasen im Detail"):
        if r.get("regimes"):
            st.dataframe(pd.DataFrame([{"Schwankungsphase": x["label"], "Ø Rendite p. a. (verrauscht)": pct(x["mean_pa"], 0),
                                        "Schwankung p. a.": pct(x["vol_pa"], 0, sign=False),
                                        "dauert typisch": f"{num(x['expected_weeks'], 0)} Wochen"}
                                       for x in r["regimes"]]), hide_index=True, use_container_width=True)
        st.caption("Das Modell teilt die Wochen in Phasen mit eigener Schwankung ein. Die Schwankung trennt es "
                   "zuverlässig, die Durchschnittsrendite je Phase ist dagegen sehr unsicher – deshalb kommt die "
                   "Richtung (aufwärts/abwärts) aus dem Trend.")

    st.subheader("Absturz vom Hoch")
    full = daily(a)["close"]
    dd_s = full / full.cummax() - 1
    fig = go.Figure(go.Scatter(x=dd_s.index, y=dd_s, fill="tozeroy", line=dict(color=c[a], width=1.5),
                               fillcolor=c["up"], name="vom Hoch", hovertemplate="%{y:.1%}<extra></extra>"))
    show(style(fig, 260, pct_axis=True))

    st.subheader("Zusammenhang zwischen den Assets")
    win = st.segmented_control("Fenster", ["90d", "1y", "5y"], default="1y", key="corr_win",
                               format_func={"90d": "90 Tage", "1y": "1 Jahr", "5y": "5 Jahre"}.get)
    cross = kv("analysis:_cross") or {}
    m = cross.get(win or "1y")
    if m:
        keys = list(ASSETS)
        z = [[m[x][y] for y in keys] for x in keys]
        fig = go.Figure(go.Heatmap(z=z, x=[NAMES[k] for k in keys], y=[NAMES[k] for k in keys], zmin=-1, zmax=1,
                                   colorscale=[[0, c["neg"]], [0.5, c["mid"]], [1, c["pos"]]],
                                   texttemplate="%{z:.2f}", hovertemplate="%{y} ↔ %{x}: %{z:.2f}<extra></extra>",
                                   xgap=2, ygap=2, colorbar=dict(thickness=10)))
        fig.update_layout(height=340, margin=dict(l=4, r=4, t=10, b=4), separators=",.",
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        fig.update_yaxes(autorange="reversed")
        show(fig)
        st.caption("+1 = bewegen sich immer gleich, 0 = unabhängig, −1 = immer gegenläufig. "
                   "Niedrige Werte = gute Mischung fürs Portfolio.")

    with st.expander("Was die Forschung zu Mustern sagt"):
        st.markdown(
            "- **Schwankung ist vorhersagbar, die Richtung kaum.** Ruhige Phasen folgen auf ruhige, wilde auf wilde.\n"
            "- **Trend (Momentum)** ist bei Krypto das robusteste Muster – aber kein Versprechen.\n"
            "- **Chartmuster und Indikatoren wie RSI** halten nach Korrektur für Data-Snooping meist nicht. "
            "Hier nur als Anzeige.\n"
            "- Ob ein Modell wirklich etwas taugt, zeigt die Seite **Training**.")


# =====================================================================
# Seite 3: Prognose
# =====================================================================
HORIZONS = {"1 Monat": 30, "3 Monate": 91, "6 Monate": 182, "1 Jahr": 365, "2 Jahre": 730, "5 Jahre": 1826,
            "10 Jahre": 3652}
DRIFTS = {1.0: "historischer Trend", 0.5: "halber Trend", 0.0: "kein Trend (nur Schwankung)"}


def recommended_drift(asset: str) -> float | None:
    bt = kv(f"backtest:{asset}") or {}
    return (bt.get("best_drift") or {}).get("drift")


def calibration_note(asset: str, horizon: int, method: str, drift: float):
    bt = kv(f"backtest:{asset}") or {}
    cand = [x for x in bt.get("calibration", []) if x["method"] == method and x["drift"] == drift]
    if not cand:
        return
    best = min(cand, key=lambda x: abs(x["horizon"] - horizon))
    st.caption(f"Selbsttest ({best['horizon']} Tage, {best['n_dates']} Prognosen aus der Vergangenheit): "
               f"Der echte Kurs lag in **{pct(best['cov90'], 0, sign=False)}** der Fälle im 90-%-Band (Ziel: 90 %), "
               f"zu tief {pct(best['below5'], 0, sign=False)}, zu hoch {pct(best['above95'], 0, sign=False)}.")


def page_forecast():
    st.title("Prognose")
    st.caption("Keine Kursvorhersage, sondern die Spanne dessen, was nach der Historie plausibel ist.")
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("Asset", list(ASSETS), format_func=NAMES.get, key="fc_asset")
    hz = c2.selectbox("Zeitraum", list(HORIZONS), index=3, key="fc_h")
    amount = c3.number_input("Betrag (€)", min_value=1.0, value=100.0, step=50.0, key="fc_amt")
    H = HORIZONS[hz]
    c4, c5 = st.columns(2)
    methods = ["Historie (Block-Bootstrap)"] + (["Aktuelle Schwankung (GARCH)"] if H <= 730 else [])
    method = c4.radio("Methode", methods, horizontal=True, key="fc_m")
    rec = recommended_drift(a)
    opts = list(DRIFTS)
    drift = c5.radio("Trend-Annahme", opts, index=opts.index(rec) if rec in opts else 1, horizontal=True,
                     format_func=lambda d: DRIFTS[d] + (" ★" if d == rec else ""), key="fc_d")
    if rec is not None:
        c5.caption("★ = im Selbsttest am besten kalibriert")
    close = daily(a)["close"].dropna()
    with st.spinner("Simuliere …"):
        if method.startswith("Aktuelle"):
            paths = forecast.garch_paths(close, H, 5000, drift=drift, seed=1)
            mkey = "garch"
        else:
            cal = closes([a])
            paths = forecast.bootstrap_paths(forecast.log_returns(cal), H, 5000, drift=drift, seed=1)[0]
            mkey = "bootstrap"
    sm = forecast.summarize(paths, amount)
    x = [pd.Timestamp.today().normalize() + pd.Timedelta(days=int(d)) for d in sm["days"]]
    show(fan_chart(x, sm["fan"], invested=[amount] * len(x), title=f"{eur(amount)} in {NAMES[a]} · {hz}"))
    cols = st.columns(5)
    for col, q in zip(cols, (5, 25, 50, 75, 95)):
        col.metric(PCT_NAMES[q], eur(sm["end"][q]), delta=delta(sm["end"][q] / amount - 1))
    st.markdown(f"Chance auf **Verlust**: {pct(sm['p_loss'], 0, sign=False)} · auf **Verdopplung**: "
                f"{pct(sm['p_double'], 0, sign=False)} · auf **−50 % oder schlimmer**: {pct(sm['p_minus50'], 0, sign=False)}")
    calibration_note(a, H, mkey, drift if mkey == "bootstrap" else (1.0 if drift >= 0.5 else 0.0))

    st.subheader("Alle Assets im Vergleich")
    rows = []
    for b in ASSETS:
        cb = closes([b])
        p = forecast.bootstrap_paths(forecast.log_returns(cb), H, 5000, drift=drift, seed=1)[0][:, -1] * amount
        rows.append({"Asset": NAMES[b], "schlecht (5 %)": eur(np.percentile(p, 5)), "typisch": eur(np.median(p)),
                     "gut (95 %)": eur(np.percentile(p, 95)), "Chance auf Verlust": pct((p < amount).mean(), 0, sign=False),
                     "Historie ab": cb.index[0].strftime("%Y")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(f"Methode Historie, {DRIFTS[drift]}, {eur(amount)}, {hz}. Ohne Gebühren und Steuern – die rechnet der Rechner.")


# =====================================================================
# Seite 4: Rechner
# =====================================================================
MODES = ["Zukunft simulieren", "Was wäre gewesen (alle Startzeitpunkte)", "Konkreter Start in der Vergangenheit",
         "Stresstest: größter Crash", "Eigenes Szenario"]


def plan_form() -> tuple[str, calc.Plan, dict] | None:
    infl = calc.inflation_stats(hicp())
    with st.form("calc_form"):
        mode = st.radio("Was willst du rechnen?", MODES, key="calc_mode")
        st.markdown("**Mix** (Anteile in %)")
        wc = st.columns(len(ASSETS))
        weights = {a: wc[i].number_input(NAMES[a], 0, 100, 100 if a == "BTC" else 0, 5, key=f"w_{a}")
                   for i, a in enumerate(ASSETS)}
        c1, c2, c3, c4 = st.columns(4)
        initial = c1.number_input("Einmalbetrag (€)", 0.0, 1e8, 100.0, 100.0)
        monthly = c2.number_input("Sparrate pro Monat (€)", 0.0, 1e7, 0.0, 25.0)
        years = c3.number_input("Laufzeit (Jahre)", 0.25, 10.0, 1.0, 0.25)
        goal = c4.number_input("Ziel (€, optional)", 0.0, 1e9, 0.0, 100.0)
        with st.expander("Sparplan-Dauer, Entnahme, Rebalancing"):
            e1, e2, e3, e4 = st.columns(4)
            contrib_m = e1.number_input("Sparplan nur für X Monate (0 = ganze Laufzeit)", 0, 120, 0)
            wd = e2.number_input("Entnahme pro Monat (€)", 0.0, 1e7, 0.0, 50.0)
            wd_from = e3.number_input("Entnahme ab Monat", 0, 120, 12)
            reb = e4.selectbox("Rebalancing", ["none", "quarterly", "yearly"],
                               format_func={"none": "keins", "quarterly": "vierteljährlich", "yearly": "jährlich"}.get)
        with st.expander("Kosten (Annahmen)"):
            costs = {}
            for a, cd in calc.default_costs().items():
                k1, k2, k3, k4 = st.columns([1.2, 1, 1, 1])
                k1.markdown(f"**{NAMES[a]}**")
                costs[a] = calc.Costs(
                    buy_fee=k2.number_input("Gebühr je Kauf/Verkauf %", 0.0, 10.0, cd.buy_fee * 100, 0.05, key=f"f_{a}") / 100,
                    spread=k3.number_input("Spread %", 0.0, 20.0, cd.spread * 100, 0.05, key=f"s_{a}") / 100,
                    custody_pa=k4.number_input("Verwahrung % p. a.", 0.0, 5.0, cd.custody_pa * 100, 0.05, key=f"c_{a}") / 100)
        with st.expander("Steuern & Inflation"):
            t1, t2, t3 = st.columns(3)
            tax = t1.selectbox("Steuer", ["none", "de", "flat"],
                               format_func={"none": "ignorieren", "de": "Deutschland (vereinfacht)",
                                            "flat": "pauschaler Satz auf Gewinn"}.get)
            rate = t2.number_input("Persönlicher Steuersatz % (Krypto/Gold < 1 Jahr) bzw. pauschaler Satz", 0.0, 60.0,
                                   35.0, 1.0) / 100
            infl_default = infl.get("10y", 0.02)
            infl_pa = t3.number_input(f"Inflation % p. a. (Ø 10 J laut HVPI: {pct(infl_default, 1, sign=False)})", -2.0,
                                      20.0, round(infl_default * 100, 1), 0.1) / 100
        with st.expander("Simulation"):
            s1, s2, s3 = st.columns(3)
            drift = s1.radio("Trend-Annahme", list(DRIFTS), index=1, format_func=DRIFTS.get)
            method = s2.radio("Methode (nur 1 Asset)", ["bootstrap", "garch"],
                              format_func={"bootstrap": "Historie", "garch": "aktuelle Schwankung (GARCH)"}.get)
            start_date = s3.date_input("Startdatum (für „Konkreter Start“)", dt.date(2021, 11, 1))
            st.markdown("**Eigenes Szenario** (gilt für alle Assets im Mix)")
            phases = st.data_editor(pd.DataFrame({"Veränderung %": [-50.0, 100.0, 0.0], "Dauer (Monate)": [3, 12, 0]}),
                                    num_rows="dynamic", hide_index=True, key="phases")
        ok = st.form_submit_button("Berechnen", type="primary")
    if not ok and "calc_last" not in st.session_state:
        return None
    if not ok:
        return st.session_state["calc_last"]
    if sum(weights.values()) <= 0:
        st.error("Mindestens ein Asset braucht einen Anteil über 0 %.")
        return None
    plan = calc.Plan(weights={a: w / 100 for a, w in weights.items()}, horizon_days=int(round(years * 365.25)),
                     initial=initial, monthly=monthly, contrib_months=contrib_m or None, withdraw_monthly=wd,
                     withdraw_from_month=wd_from if wd > 0 else None, rebalance=reb, costs=costs, tax=tax,
                     personal_rate=rate, flat_rate=rate, inflation_pa=infl_pa, goal=goal or None)
    extra = {"drift": drift, "method": method, "start_date": start_date,
             "phases": [(float(r["Veränderung %"]), float(r["Dauer (Monate)"])) for _, r in phases.dropna().iterrows()
                        if r["Dauer (Monate)"] > 0]}
    st.session_state["calc_last"] = (mode, plan, extra)
    return mode, plan, extra


def result_block(res: dict, plan: calc.Plan, x, title: str):
    inv = res["invested"]
    single = res["n"] == 1  # ein einzelner Verlauf (konkreter Start, Stresstest, eigenes Szenario)
    if single:
        v = res["after_tax"][50]
        h = st.columns(4)
        h[0].metric("Ergebnis nach Steuern", eur(v), delta=delta(v / inv - 1) if inv else None)
        h[1].metric("Gewinn / Verlust", eur(res["profit"][50]))
        h[2].metric("Schlimmster Moment", pct(res["worst_moment"][50], 0),
                    help="Tiefster Stand unterwegs gegenüber dem eingezahlten Geld – wichtig fürs Durchhalten.")
        h[3].metric("Größter Rückgang des Depots", pct(res["max_dd"][50], 0))
    else:
        h = st.columns(5)
        h[0].metric("Typisch nach Steuern", eur(res["after_tax"][50]),
                    delta=delta(res["after_tax"][50] / inv - 1) if inv else None)
        h[1].metric("schlecht (5 %)", eur(res["after_tax"][5]),
                    delta=delta(res["after_tax"][5] / inv - 1) if inv else None)
        h[2].metric("gut (95 %)", eur(res["after_tax"][95]),
                    delta=delta(res["after_tax"][95] / inv - 1) if inv else None)
        h[3].metric("Chance auf Verlust", pct(res["p_loss"], 0, sign=False))
        h[4].metric("Schlimmster Moment (typisch)", pct(res["worst_moment"][50], 0),
                    help="Tiefster Stand unterwegs gegenüber dem eingezahlten Geld – wichtig fürs Durchhalten.")
    notes = [f"Eingezahlt: **{eur(inv)}**", f"Break-even nach Kosten: Kurse müssen um **{pct(res['break_even_pct'])}** steigen"]
    if res["p_goal"] is not None:
        notes.append(f"Ziel {eur(plan.goal)}: **{'erreicht' if res['p_goal'] else 'nicht erreicht'}**" if single else
                     f"Ziel {eur(plan.goal)} erreicht in **{pct(res['p_goal'], 0, sign=False)}** der Fälle")
    if res["ran_out_share"] is not None:
        if single:
            notes.append("Entnahme: Geld reicht " + (f"nur bis Monat **{num(res['ran_out_month_median'])}**"
                                                      if res["ran_out_share"] else "**bis zum Ende**"))
        else:
            notes.append(f"Entnahme: Geld reicht nicht in **{pct(res['ran_out_share'], 0, sign=False)}** der Fälle"
                         + (f" (typisch ab Monat {num(res['ran_out_month_median'])})" if res["ran_out_month_median"] else ""))
    notes.append(f"Real (Kaufkraft heute, {pct(plan.inflation_pa, 1, sign=False)} Inflation): "
                 f"{'' if single else 'typisch '}**{eur(res['real_after_tax'][50])}**")
    st.markdown(" · ".join(notes))
    if single:
        c = C()
        st.markdown(f"**{title}**")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=res["fan"]["pct"][50], line=dict(color=c["median"], width=2), name="Depotwert",
                                 hovertemplate="Depot %{y:,.0f} €<extra></extra>"))
        fig.add_trace(go.Scatter(x=x, y=res["fan"]["invested"], line=dict(color=c["neutral"], width=2), name="eingezahlt",
                                 hovertemplate="eingezahlt %{y:,.0f} €<extra></extra>"))
        show(style(fig, 380, money=True))
    else:
        show(fan_chart(x, res["fan"]["pct"], invested=res["fan"]["invested"], title=title))
    rows = [("Depotwert", res["final_value"]), ("Auszahlung nach Verkauf", res["payout"]), ("Steuer", res["tax"]),
            ("nach Steuern", res["after_tax"]), ("nach Steuern, real", res["real_after_tax"]),
            ("Gewinn (inkl. Entnahmen)", res["profit"]), ("entnommen", res["withdrawn"])]
    qs = (50,) if single else (5, 25, 50, 75, 95)
    cols = {50: "Ergebnis"} if single else PCT_NAMES
    tab = pd.DataFrame({name: {cols[q]: eur(v[q]) for q in qs} for name, v in rows}).T
    tab.loc["schlimmster Moment"] = [pct(res["worst_moment"][q], 0) for q in qs]
    tab.loc["größter Rückgang des Depots"] = [pct(res["max_dd"][q], 0) for q in qs]
    st.dataframe(tab, use_container_width=True)


def page_calculator():
    st.title("Rechner")
    got = plan_form()
    if not got:
        st.info("Eingaben wählen und **Berechnen** drücken.")
        return
    mode, plan, extra = got
    assets = [a for a, w in plan.weights.items() if w > 0]
    H = plan.horizon_days
    today = pd.Timestamp.today().normalize()
    with st.spinner("Rechne …"):
        if mode == MODES[0]:
            if extra["method"] == "garch" and len(assets) == 1:
                p = forecast.garch_paths(daily(assets[0])["close"].dropna(), H, forecast.sims_for(H, 1, 2e7),
                                         drift=extra["drift"], seed=3)
                paths = {assets[0]: p}
            else:
                if extra["method"] == "garch":
                    st.info("GARCH geht nur mit einem Asset – rechne mit der Historie.")
                cl = closes(assets)
                P = forecast.bootstrap_paths(forecast.log_returns(cl), H, forecast.sims_for(H, len(assets), 2e7),
                                             drift=extra["drift"], seed=3)
                paths = {a: P[i] for i, a in enumerate(assets)}
                if len(assets) > 1:
                    st.caption(f"Gemeinsame Historie aller gewählten Assets ab {cl.index[0]:%m/%Y}.")
            res = calc.simulate(plan, paths)
            x = [today + pd.Timedelta(days=int(d)) for d in res["fan"]["days"]]
            result_block(res, plan, x, f"Simulation · {res['n']} Pfade")
        elif mode == MODES[1]:
            cl = closes(assets)
            P, starts = forecast.historical_windows(cl, H, step=7)
            res = calc.simulate(plan, {a: P[i] for i, a in enumerate(assets)})
            x = [d / 30.4375 for d in res["fan"]["days"]]
            result_block(res, plan, x, f"{res['n']} echte Startzeitpunkte seit {starts[0]:%m/%Y} · x-Achse: Monate")
            st.subheader("Ergebnis je Startzeitpunkt")
            per = pd.Series(res["per_sim"]["profit"] / max(res["invested"], 1e-9), index=pd.DatetimeIndex(starts))
            c = C()
            fig = go.Figure(go.Scatter(x=per.index, y=per, line=dict(color=c[assets[0]] if len(assets) == 1 else c["median"],
                                                                         width=2), name="Gewinn/Verlust",
                                       hovertemplate="Start %{x|%d.%m.%Y}: %{y:+.0%}<extra></extra>"))
            fig.add_hline(y=0, line=dict(color=c["neutral"], width=1))
            show(style(fig, 300, pct_axis=True))
            st.markdown(f"Bester Start: **{per.idxmax():%d.%m.%Y}** ({pct(per.max(), 0)}) · schlechtester Start: "
                        f"**{per.idxmin():%d.%m.%Y}** ({pct(per.min(), 0)}) · Anteil mit Verlust: "
                        f"{pct((per < 0).mean(), 0, sign=False)}")
        elif mode == MODES[2]:
            cl = closes(assets)
            sd = pd.Timestamp(extra["start_date"])
            cl = cl[cl.index >= sd]
            if len(cl) < 2:
                st.error("Startdatum liegt außerhalb der Daten.")
                return
            P = (cl.values / cl.values[0]).T[:, None, :]
            plan.horizon_days = len(cl) - 1
            defl = calc.deflate_path(hicp(), cl.index[0], cl.index[-1])
            plan.inflation_pa = defl ** (365.25 / max(plan.horizon_days, 1)) - 1
            res = calc.simulate(plan, {a: P[i] for i, a in enumerate(assets)})
            x = [cl.index[0] + pd.Timedelta(days=int(d)) for d in res["fan"]["days"]]
            st.caption(f"Echter Verlauf {cl.index[0]:%d.%m.%Y} → {cl.index[-1]:%d.%m.%Y}, echte Inflation "
                       f"{pct(plan.inflation_pa, 1, sign=False)} p. a. (Laufzeit-Eingabe wird ignoriert).")
            result_block(res, plan, x, "Echter Verlauf")
        elif mode == MODES[3]:
            cl = closes(assets)
            lead = max(assets, key=lambda a: plan.weights[a])
            P, peak, h = forecast.stress_path(cl, lead, H)
            if h < H:
                st.warning(f"Nach dem Crash gibt es nur {h} Tage Daten – Laufzeit gekürzt.")
                plan.horizon_days = h
            res = calc.simulate(plan, {a: P[i] for i, a in enumerate(assets)})
            x = [peak + pd.Timedelta(days=int(d)) for d in res["fan"]["days"]]
            st.caption(f"Du steigst am {peak:%d.%m.%Y} ein – am Gipfel vor dem größten Crash von {NAMES[lead]}. "
                       f"Alle Assets laufen mit ihren echten Kursen ab diesem Tag.")
            result_block(res, plan, x, "Stresstest")
        else:
            if not extra["phases"]:
                st.error("Mindestens eine Phase mit Dauer > 0 eintragen.")
                return
            P = forecast.custom_path(extra["phases"], H, len(assets))
            res = calc.simulate(plan, {a: P[i] for i, a in enumerate(assets)})
            x = [today + pd.Timedelta(days=int(d)) for d in res["fan"]["days"]]
            st.caption("Eigenes Szenario: " + ", danach ".join(f"{pct(p / 100, 0)} in {num(m, 0)} Monaten"
                                                               for p, m in extra["phases"]) + ", dann seitwärts.")
            result_block(res, plan, x, "Eigenes Szenario")
    with st.expander("Vereinfachungen"):
        st.markdown(calc.__doc__)


# =====================================================================
# Seite 5: Training
# =====================================================================
def page_training():
    st.title("Training & Selbsttest")
    st.caption("Walk-Forward: Jede Prognose wurde nur mit Daten bis zu ihrem Datum gemacht und dann mit dem "
               "echten Verlauf verglichen. Läuft jede Nacht neu.")
    a = st.selectbox("Asset", list(ASSETS), format_func=NAMES.get, key="tr_asset")
    bt = kv(f"backtest:{a}")
    if not bt:
        st.info("Noch kein Backtest – läuft nachts (oder: python -m invest backtest).")
        return
    st.caption(f"Stand: {dt.datetime.fromtimestamp(bt['updated']):%d.%m.%Y %H:%M}")

    st.subheader("1. Treffen die Prognose-Spannen?")
    cal = bt["calibration"]
    tab = pd.DataFrame([{
        "Zeitraum": f"{x['horizon']} Tage", "Methode": "GARCH" if x["method"] == "garch" else "Historie",
        "Trend": DRIFTS.get(x["drift"], x["drift"]), "Prognosen": x["n_dates"],
        "im 90-%-Band (Ziel 90 %)": pct(x["cov90"], 0, sign=False),
        "im 50-%-Band (Ziel 50 %)": pct(x["cov50"], 0, sign=False),
        "zu tief": pct(x["below5"], 0, sign=False), "zu hoch": pct(x["above95"], 0, sign=False),
        "Median vs. „keine Änderung“": pct(x["skill_vs_rw"], 0),
    } for x in cal])
    st.dataframe(tab, hide_index=True, use_container_width=True)
    bd = bt.get("best_drift") or {}
    if bd:
        st.markdown(f"Am besten kalibriert: **{DRIFTS.get(bd['drift'])}** – diese Annahme ist im Rechner und in der "
                    f"Prognose mit ★ markiert. „Median vs. keine Änderung“ > 0 % heißt: Die Mitte der Spanne lag "
                    f"näher am echten Kurs als die Annahme „bleibt gleich“.")
    labels = [f"{x['horizon']} Tage · {'GARCH' if x['method'] == 'garch' else 'Historie'} · {DRIFTS.get(x['drift'])}"
              for x in cal]
    pick = st.selectbox("Verlauf ansehen", range(len(cal)), format_func=lambda i: labels[i], index=0)
    s = cal[pick]["series"]
    c = C()
    x = pd.to_datetime(s["date"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=s["p95"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=s["p5"], fill="tonexty", fillcolor=c["band_outer"], line=dict(width=0),
                             name="vorhergesagte Spanne (5–95 %)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=s["p50"], line=dict(color=c["median"], width=2), name="Mitte der Prognose",
                             hovertemplate="Prognose ×%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=s["actual"], mode="markers", marker=dict(size=8, color=c[a]),
                             name="was wirklich passierte", hovertemplate="echt ×%{y:.2f}<extra></extra>"))
    fig.update_yaxes(type="log", title="Kurs-Faktor (×1 = unverändert)",
                     tickvals=[0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10], ticktext=["×0,25", "×0,5", "×0,75", "×1", "×1,5",
                                                                              "×2", "×3", "×5", "×10"])
    show(style(fig, 360))
    st.caption("x-Achse: Datum, an dem die Prognose gemacht wurde. Punkte außerhalb der Fläche = Spanne lag daneben.")

    st.subheader("2. Kann ein Modell die Richtung vorhersagen?")
    for d in bt["direction"]:
        st.markdown(f"**Steigt der Kurs in {d['horizon_days']} Tagen?** Test ab {d['test_from']}, "
                    f"{num(d['n_test'])} Tage (≈ {num(d['n_eff'])} unabhängige Fälle). Basis: in "
                    f"{pct(d['base_rate_up'], 0, sign=False)} der Fälle stieg der Kurs.")
        bh = d["buy_hold"]
        rows = [{"Modell": "Buy & Hold (immer investiert)", "Trefferquote": "–", "besser als Basis?": "–",
                 "investiert": "100 %", "Rendite p. a. nach Kosten": pct(bh["cagr"]), "größter Rückgang": pct(bh["max_dd"]),
                 "Sharpe": num(bh["sharpe"], 2), "Trades": bh["trades"], "freigegeben": "–"}]
        for m in d["models"]:
            st_ = m["strategy"]
            rows.append({"Modell": m["name"], "Trefferquote": pct(m["hit"], 1, sign=False),
                         "besser als Basis?": ("ja" if (m["z_vs_base"] or 0) > 1.96 else "nein") + f" (z={num(m['z_vs_base'], 1)})",
                         "investiert": pct(m["share_invested"], 0, sign=False),
                         "Rendite p. a. nach Kosten": pct(st_["cagr"]), "größter Rückgang": pct(st_["max_dd"]),
                         "Sharpe": num(st_["sharpe"], 2), "Trades": st_["trades"],
                         "freigegeben": "✅ ja" if m["approved"] else "❌ nein"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("Freigegeben = Trefferquote signifikant über der Basis UND die Strategie (investiert, wenn das Modell "
               "„steigt“ sagt) hat nach Gebühren ein besseres Rendite-Risiko-Verhältnis (Sharpe) als Buy & Hold.")

    st.subheader("3. Minuten-Muster")
    ip = bt.get("intraday") or {}
    if ip:
        st.markdown(f"Stündliche Kurse seit {ip['since']} ({num(ip['hours'])} Stunden): Nach einer steigenden Stunde "
                    f"folgte im Schnitt **{num(ip['next_after_up_bp'], 2)} Basispunkte**, nach einer fallenden "
                    f"**{num(ip['next_after_down_bp'], 2)}**. Gleiche Richtung wie die Stunde davor: "
                    f"{pct(ip['same_direction'], 1, sign=False)}. Ein Kauf und Verkauf kostet ca. "
                    f"**{num(ip['roundtrip_cost_bp'], 0)} Basispunkte** – ein Muster müsste also deutlich größer sein, "
                    f"um nutzbar zu sein.")
    else:
        st.caption("Zu wenig 5-Minuten-Daten (Archiv wächst ab jetzt).")


# =====================================================================
# Seite 6: Alarme
# =====================================================================
def page_alerts():
    st.title("Alarme")
    conf = telegram_conf()
    if conf.get("TELEGRAM_TOKEN") and conf.get("TELEGRAM_CHAT_ID"):
        st.success("Telegram ist verbunden.")
    else:
        st.warning("Telegram ist noch nicht eingerichtet – Alarme werden nur hier gespeichert.")
    s = settings()["alerts"]
    st.dataframe(pd.DataFrame([{"Asset": NAMES[a], "Sturz/Sprung in 1 h ab": f"{num(s[a]['move_1h'], 1)} %",
                                "in 24 h ab": f"{num(s[a]['move_24h'], 1)} %"} for a in ASSETS]),
                 hide_index=True, use_container_width=True)
    st.caption(f"Außerdem: ungewöhnliche Stunde (≥ {num(s['zscore_1h'], 0)}× normale Schwankung), neues Allzeithoch, "
               f"neue Marktphase, Tagesbericht um {s['daily_report_hour']}:00 Uhr. Sperrzeit {num(s['cooldown_hours'], 0)} h. "
               f"Ändern in /root/.config/invest-tool/settings.toml.")
    al = query("SELECT ts, asset, rule, message, sent FROM alerts ORDER BY ts DESC LIMIT 100")
    if al.empty:
        st.caption("Noch keine Alarme.")
        return
    al["Zeit"] = pd.to_datetime(al["ts"], unit="s").dt.tz_localize("UTC").dt.tz_convert("Europe/Berlin").dt.strftime("%d.%m.%Y %H:%M")
    al["Nachricht"] = al["message"].map(lambda m: strip_html(m).replace("\n", " · "))
    al["Telegram"] = al["sent"].map({1: "✅", 0: "–"})
    st.dataframe(al[["Zeit", "asset", "Nachricht", "Telegram"]].rename(columns={"asset": "Asset"}), hide_index=True,
                 use_container_width=True)


# ---------- Seitenleiste & Navigation ----------
def sidebar():
    runs = query("SELECT job, MAX(ts) AS ts, ok FROM runs GROUP BY job")
    with st.sidebar:
        st.markdown("**Datenstand**")
        for _, r in runs.iterrows():
            st.caption(f"{'✅' if r.ok else '⚠️'} {r.job}: {age_text(int(r.ts))}")
        if st.button("Neu laden"):
            st.cache_data.clear()
            st.rerun()


pages = [st.Page(page_overview, title="Übersicht", icon="🏠", default=True),
         st.Page(page_patterns, title="Chart & Muster", icon="📊", url_path="muster"),
         st.Page(page_forecast, title="Prognose", icon="🔮", url_path="prognose"),
         st.Page(page_calculator, title="Rechner", icon="🧮", url_path="rechner"),
         st.Page(page_training, title="Training", icon="🧪", url_path="training"),
         st.Page(page_alerts, title="Alarme", icon="🔔", url_path="alarme")]
sidebar()
if __import__("os").environ.get("INVEST_PAGE"):  # nur für automatische Tests
    globals()[__import__("os").environ["INVEST_PAGE"]]()
else:
    st.navigation(pages).run()
