"""Einfache Rechner mit fester Rendite (wie bei Finanzfluss): Zinseszins, Entnahme, Inflation, Kosten, Verlust."""
from __future__ import annotations

import pandas as pd

from .. import db, markets, service
from ..config import ASSETS
from ..units import INFLATION_GEOS, fmt_money, fmt_num
from . import CalcError, calculator, cur_of, money_field, pct_field, years_field


def _pct(x: float, d: int = 1) -> str:
    return f"{fmt_num(x * 100, d)} %"


def grow(start: float, monthly: float, years: float, rate: float, dyn: float = 0.0, cost: float = 0.0,
         front: float = 0.0) -> list[dict]:
    """Monatlich: Einzahlung am Monatsanfang, dann Wertentwicklung. Ergebnis je Jahr."""
    i = (1 + rate) ** (1 / 12) * (1 - cost) ** (1 / 12) - 1
    bal = start * (1 - front)
    paid = start
    rate_m = monthly
    rows = []
    for y in range(1, int(round(years)) + 1):
        for _ in range(12):
            bal += rate_m * (1 - front)
            paid += rate_m
            bal *= 1 + i
        rows.append({"year": y, "paid": paid, "value": bal})
        rate_m *= 1 + dyn
    return rows


# ---------- Zinseszins ----------
@calculator("compound", "Zinseszins-Rechner", "Wie wächst mein Geld bei fester Rendite?", "build", [
    money_field("start", "Startbetrag", 1000), money_field("monthly", "Sparrate pro Monat", 100),
    years_field("years", "Dauer in Jahren", 20),
    pct_field("rate", "Rendite pro Jahr", 5.0, "Zum Vergleich: Tagesgeld 1–3 %, Welt-Aktien langfristig etwa 5–7 % "
              "(mit Schwankungen), Anleihen 2–3 %.", lo=-10, hi=30),
    pct_field("dyn", "Sparrate jährlich erhöhen um", 0.0, "Dynamik: z. B. 2 % pro Jahr, wenn dein Gehalt steigt.",
              lo=0, hi=20),
], icon="🌱", order=30)
def compound(con, profile, v):
    cur = cur_of(profile)
    rows = grow(v["start"] or 0, v["monthly"] or 0, v["years"], v["rate"] / 100, v["dyn"] / 100)
    end = rows[-1]
    gain = end["value"] - end["paid"]
    share = gain / end["value"] if end["value"] > 0 else 0
    last_gain = end["value"] - (rows[-2]["value"] if len(rows) > 1 else (v["start"] or 0)) - \
        (end["paid"] - (rows[-2]["paid"] if len(rows) > 1 else (v["start"] or 0)))
    text = [f"Nach {int(v['years'])} Jahren hast du etwa {fmt_money(end['value'], cur)}.",
            f"Davon zahlst du {fmt_money(end['paid'], cur)} selbst ein – {fmt_money(gain, cur)} sind Erträge "
            f"({_pct(share, 0)} des Endbetrags)."]
    if v["monthly"] and last_gain > v["monthly"] * 12:
        text.append(f"Im letzten Jahr bringen die Erträge ({fmt_money(last_gain, cur)}) mehr als deine Einzahlungen – "
                    "das ist der Zinseszins-Effekt.")
    text.append("Echte Anlagen schwanken: Die feste Rendite ist ein Durchschnitt. Wie breit die Spanne sein kann, "
                "zeigt „Was könnte daraus werden?“.")
    return {
        "tiles": [{"label": "Endbetrag", "value": fmt_money(end["value"], cur), "tone": "accent"},
                  {"label": "Eingezahlt", "value": fmt_money(end["paid"], cur)},
                  {"label": "Erträge", "value": fmt_money(gain, cur), "tone": "good" if gain >= 0 else "bad"}],
        "text": text,
        "charts": [{"type": "stack", "title": "Eingezahlt und Erträge je Jahr", "unit": "money",
                    "x": [f"{r['year']}" for r in rows],
                    "series": [{"name": "Eingezahlt", "role": "invest", "data": [round(r["paid"], 2) for r in rows]},
                               {"name": "Erträge", "role": "depot",
                                "data": [round(r["value"] - r["paid"], 2) for r in rows]}]}],
        "table": {"head": ["Jahr", "Eingezahlt", "Wert"],
                  "rows": [[r["year"], fmt_money(r["paid"], cur), fmt_money(r["value"], cur)] for r in rows]},
        "notes": ["Vor Steuern und Inflation. Einzahlung jeweils am Monatsanfang."],
        "actions": [{"label": "Mit echten Kursen rechnen", "type": "link",
                     "href": f"#/rechner/future?initial={v['start'] or 0}&monthly={v['monthly'] or 0}&years={int(min(v['years'], 40))}"}],
    }


# ---------- Entnahme ----------
def _drain(capital: float, w: float, rate: float, infl: float, max_years: int = 100) -> list[dict]:
    i = (1 + rate) ** (1 / 12) - 1
    bal, rows, total = capital, [], 0.0
    for y in range(1, max_years + 1):
        for _ in range(12):
            take = min(w, max(bal, 0))
            bal -= take
            total += take
            bal *= 1 + i
        rows.append({"year": y, "value": max(bal, 0.0), "withdraw": w * 12, "total": total})
        if bal <= 0:
            break
        w *= 1 + infl
    return rows


def _final(capital: float, w: float, rate: float, infl: float, years: int) -> float:
    i = (1 + rate) ** (1 / 12) - 1
    bal = capital
    for _ in range(years):
        for _ in range(12):
            bal = (bal - w) * (1 + i)
        w *= 1 + infl
    return bal


@calculator("withdraw", "Wie lange reicht mein Geld?", "Entnahmeplan: monatlich Geld aus dem Depot nehmen – "
            "wie lange reicht es, oder wie viel geht pro Monat?", "plan", [
                {"key": "mode", "label": "Was möchtest du wissen?", "type": "select", "default": "duration",
                 "options": [["duration", "Wie lange reicht mein Geld?"], ["amount", "Wie viel kann ich entnehmen?"]]},
                money_field("capital", "Vermögen zu Beginn", 100000),
                money_field("withdraw", "Entnahme pro Monat", 600, show_if={"mode": "duration"}),
                years_field("years", "Über wie viele Jahre?", 25, show_if={"mode": "amount"}),
                pct_field("rate", "Rendite pro Jahr während der Entnahme", 4.0, lo=-5, hi=15),
                pct_field("infl", "Entnahme jährlich erhöhen (Inflation)", 2.0, lo=0, hi=10),
            ], icon="🏖️", order=30)
def withdraw(con, profile, v):
    cur = cur_of(profile)
    cap, rate, infl = v["capital"] or 0, v["rate"] / 100, v["infl"] / 100
    if cap <= 0:
        raise CalcError("Bitte ein Vermögen eingeben.")
    if v["mode"] == "amount":
        n = int(v["years"])
        lo, hi = 0.0, cap
        for _ in range(80):  # größte Entnahme, bei der nach n Jahren nichts mehr übrig ist
            mid = (lo + hi) / 2
            if _final(cap, mid, rate, infl, n) > 0:
                lo = mid
            else:
                hi = mid
        w = lo
        rows = _drain(cap, w, rate, infl, n)
        forever = cap * ((1 + rate) ** (1 / 12) - 1) if rate > infl else 0.0
        text = [f"Du kannst {int(n)} Jahre lang etwa {fmt_money(w, cur)} im Monat entnehmen"
                + (f", jedes Jahr um {_pct(infl)} mehr." if infl else ".") + " Danach ist das Geld aufgebraucht.",
                f"Insgesamt bekommst du {fmt_money(rows[-1]['total'], cur)} ausgezahlt."]
        if forever:
            text.append(f"Nur die Erträge (das Vermögen bleibt erhalten): etwa {fmt_money(forever, cur)} im Monat"
                        + (" – ohne Inflationsausgleich." if infl else "."))
        tiles = [{"label": "Entnahme pro Monat", "value": fmt_money(w, cur), "tone": "accent"},
                 {"label": "Ausgezahlt gesamt", "value": fmt_money(rows[-1]["total"], cur)},
                 {"label": "Nur Erträge", "value": fmt_money(forever, cur) if forever else "–"}]
    else:
        w = v["withdraw"] or 0
        if w <= 0:
            raise CalcError("Bitte eine Entnahme eingeben.")
        rows = _drain(cap, w, rate, infl)
        lasting = rows[-1]["value"] > 0
        years = len(rows)
        if lasting:
            text = [f"Dein Geld reicht über 100 Jahre – die Erträge decken die Entnahme von {fmt_money(w, cur)}."]
        else:
            text = [f"Bei {fmt_money(w, cur)} im Monat reicht dein Geld etwa {years} Jahre"
                    + (f" (Entnahme steigt jedes Jahr um {_pct(infl)})." if infl else ".")]
        text.append(f"Bis dahin bekommst du {fmt_money(rows[-1]['total'], cur)} ausgezahlt – "
                    f"{fmt_money(max(rows[-1]['total'] - cap, 0), cur)} mehr als dein Startvermögen.")
        text.append("Faustregel: Wer pro Jahr etwa 3–4 % des Startvermögens entnimmt, kommt mit breit gestreuten "
                    "Aktien in den meisten historischen Fällen 30 Jahre aus.")
        tiles = [{"label": "Reicht für", "value": "über 100 Jahre" if lasting else f"{years} Jahre", "tone": "accent"},
                 {"label": "Ausgezahlt gesamt", "value": fmt_money(rows[-1]["total"], cur)},
                 {"label": "Entnahmerate", "value": _pct(w * 12 / cap)}]
    return {
        "tiles": tiles, "text": text,
        "charts": [{"type": "line", "title": "Restvermögen", "unit": "money", "x": [str(r["year"]) for r in rows],
                    "series": [{"name": "Restvermögen", "role": "depot", "data": [round(r["value"], 2) for r in rows]}]}],
        "table": {"head": ["Jahr", "Entnahme im Jahr", "Rest"],
                  "rows": [[r["year"], fmt_money(r["withdraw"], cur), fmt_money(r["value"], cur)] for r in rows[:60]]},
        "notes": ["Feste Rendite, keine Steuern. Echte Märkte schwanken – schlechte Jahre am Anfang der Entnahme wiegen "
                  "schwerer (Reihenfolge-Risiko)."],
    }


# ---------- Inflation ----------
def _inflation_fields(profile):
    geo = (profile or {}).get("settings", {}).get("inflation_geo", "DE")
    return [
        {"key": "mode", "label": "Richtung", "type": "select", "default": "future",
         "options": [["future", "Was ist mein Geld in Zukunft wert?"], ["past", "Was war mein Geld früher wert?"]]},
        money_field("amount", "Betrag", 1000),
        years_field("years", "In wie vielen Jahren?", 10, show_if={"mode": "future"}),
        pct_field("rate", "Inflation pro Jahr", 2.0, "Ziel der EZB: 2 %. Leer lassen = Durchschnitt der letzten 10 Jahre.",
                  lo=0, hi=20, show_if={"mode": "future"}),
        {"key": "since", "label": "Seit Jahr", "type": "number", "default": 2000, "min": 1997, "max": 2026, "step": 1,
         "show_if": {"mode": "past"}},
        {"key": "geo", "label": "Land", "type": "select", "default": geo,
         "options": [[k, v] for k, v in INFLATION_GEOS.items()]},
    ]


@calculator("inflation", "Inflationsrechner", "Wie viel ist mein Geld in Zukunft noch wert – und was war es früher?",
            "past", _inflation_fields, icon="🎈", order=20)
def inflation(con, profile, v):
    cur = cur_of(profile)
    amount = v["amount"] or 0
    geo = v["geo"] or "DE"
    if v["mode"] == "past":
        h = db.load_hicp(con, geo)
        if h.empty:
            raise CalcError("Keine Inflationsdaten vorhanden.")
        since = int(v["since"] or 2000)
        hs = h[h.index.year >= since]
        if hs.empty:
            raise CalcError("Für dieses Jahr gibt es keine Daten.")
        ratio = float(h.iloc[-1] / hs.iloc[0])
        yrs = (h.index[-1] - hs.index[0]).n / 12
        pa = ratio ** (1 / yrs) - 1 if yrs > 0 else 0
        yearly = hs.groupby(hs.index.year).last()
        text = [f"{fmt_money(amount, cur)} aus dem Jahr {since} entsprechen heute etwa {fmt_money(amount * ratio, cur)}.",
                f"Die Preise sind um {_pct(ratio - 1, 0)} gestiegen – im Schnitt {_pct(pa)} pro Jahr "
                f"({INFLATION_GEOS.get(geo, geo)}).",
                f"Wer {fmt_money(amount, cur)} seit {since} unverzinst liegen ließ, kann heute nur noch so viel kaufen wie "
                f"damals mit {fmt_money(amount / ratio, cur)}."]
        x = [str(y) for y in yearly.index]
        data = [round(amount * float(h.iloc[-1] / yv), 2) for yv in yearly.values]
        return {"tiles": [{"label": "Heute nötig", "value": fmt_money(amount * ratio, cur), "tone": "accent"},
                          {"label": "Preisanstieg", "value": _pct(ratio - 1, 0)},
                          {"label": "Pro Jahr", "value": _pct(pa)}],
                "text": text,
                "charts": [{"type": "line", "title": f"Was {fmt_money(amount, cur)} aus dem jeweiligen Jahr heute wert sind",
                            "unit": "money", "x": x, "series": [{"name": "heutiger Wert", "role": "depot", "data": data}]}],
                "notes": ["Harmonisierter Verbraucherpreisindex (HVPI) von Eurostat, monatlich."]}
    rate = v["rate"]
    if rate is None:
        rate = service._infl_pa(con, geo) * 100
    r = rate / 100
    years = int(v["years"])
    rows = [{"year": y, "real": amount / (1 + r) ** y, "needed": amount * (1 + r) ** y} for y in range(0, years + 1)]
    end = rows[-1]
    return {
        "tiles": [{"label": f"Kaufkraft in {years} Jahren", "value": fmt_money(end["real"], cur), "tone": "bad"},
                  {"label": "Nötig für gleiche Kaufkraft", "value": fmt_money(end["needed"], cur)},
                  {"label": "Verlust", "value": _pct(1 - end["real"] / amount if amount else 0, 0)}],
        "text": [f"Bei {_pct(r)} Inflation kannst du dir in {years} Jahren mit {fmt_money(amount, cur)} nur noch so viel "
                 f"kaufen wie heute mit {fmt_money(end['real'], cur)}.",
                 f"Um die Kaufkraft zu halten, müsste das Geld bis dahin auf {fmt_money(end['needed'], cur)} wachsen – "
                 f"also mindestens {_pct(r)} pro Jahr nach Steuern bringen."],
        "charts": [{"type": "line", "title": "Kaufkraft deines Geldes", "unit": "money", "x": [str(x["year"]) for x in rows],
                    "series": [{"name": "Kaufkraft", "role": "depot", "data": [round(x["real"], 2) for x in rows]},
                               {"name": "Nötig für gleiche Kaufkraft", "role": "invest",
                                "data": [round(x["needed"], 2) for x in rows]}]}],
    }


# ---------- Kostenvergleich ----------
@calculator("costs", "Was kosten mich Gebühren?", "Vergleich: günstiger ETF gegen teuren Fonds – wie viel fressen "
            "laufende Kosten und Ausgabeaufschlag?", "cost", [
                money_field("start", "Startbetrag", 5000), money_field("monthly", "Sparrate pro Monat", 200),
                years_field("years", "Dauer in Jahren", 25),
                pct_field("rate", "Rendite pro Jahr vor Kosten", 6.0, lo=-5, hi=20),
                pct_field("cost_a", "Kosten A pro Jahr (z. B. ETF)", 0.2, "Laufende Kosten (TER) eines Welt-ETFs: 0,1–0,3 %.",
                          lo=0, hi=5, step=0.01),
                pct_field("front_a", "Ausgabeaufschlag A", 0.0, lo=0, hi=10),
                pct_field("cost_b", "Kosten B pro Jahr (z. B. aktiver Fonds)", 1.8,
                          "Aktive Fonds und Fondspolicen: oft 1,5–2,5 % pro Jahr.", lo=0, hi=5, step=0.01),
                pct_field("front_b", "Ausgabeaufschlag B", 5.0, "Bei Bank-Fonds oft 5 % auf jede Einzahlung.", lo=0, hi=10),
            ], icon="💸", order=30)
def costs(con, profile, v):
    cur = cur_of(profile)
    a = grow(v["start"] or 0, v["monthly"] or 0, v["years"], v["rate"] / 100, cost=v["cost_a"] / 100,
             front=v["front_a"] / 100)
    b = grow(v["start"] or 0, v["monthly"] or 0, v["years"], v["rate"] / 100, cost=v["cost_b"] / 100,
             front=v["front_b"] / 100)
    diff = a[-1]["value"] - b[-1]["value"]
    paid = a[-1]["paid"]
    return {
        "tiles": [{"label": "Endbetrag A", "value": fmt_money(a[-1]["value"], cur), "tone": "accent"},
                  {"label": "Endbetrag B", "value": fmt_money(b[-1]["value"], cur)},
                  {"label": "Kosten-Unterschied", "value": fmt_money(diff, cur), "tone": "bad"}],
        "text": [f"Mit A hast du nach {int(v['years'])} Jahren {fmt_money(a[-1]['value'], cur)}, mit B "
                 f"{fmt_money(b[-1]['value'], cur)}.",
                 f"Der Unterschied von {fmt_money(diff, cur)} geht allein an Gebühren – das sind "
                 f"{_pct(diff / paid if paid else 0, 0)} deiner Einzahlungen.",
                 "Ein teurer Fonds muss also jedes Jahr deutlich besser sein als der Markt, nur um gleichzuziehen. "
                 "Das schaffen langfristig nur wenige."],
        "charts": [{"type": "line", "title": "Vermögen mit A und B", "unit": "money", "x": [str(r["year"]) for r in a],
                    "series": [{"name": "A (günstig)", "role": "depot", "data": [round(r["value"], 2) for r in a]},
                               {"name": "B (teuer)", "role": "c2", "data": [round(r["value"], 2) for r in b]},
                               {"name": "Eingezahlt", "role": "invest", "data": [round(r["paid"], 2) for r in a]}]}],
        "notes": ["Gleiche Rendite vor Kosten für beide. Vor Steuern."],
    }


# ---------- Verlust aufholen ----------
def _loss_fields(profile):
    opts = [[a, ASSETS[a]["name"]] for a in ASSETS if ASSETS[a]["kind"] in ("etf", "metal", "crypto", "stock")]
    return [pct_field("loss", "Verlust", 30.0, lo=1, hi=99, step=1),
            {"key": "asset", "label": "Echte Abstürze ansehen von", "type": "market", "default": "MSCIW",
             "options": opts}]


@calculator("loss", "Wie viel muss es wieder steigen?", "Nach einem Verlust: Wie viel Plus braucht es bis zum alten "
            "Stand – und wie lange hat das früher gedauert?", "learn", _loss_fields, icon="🧗", order=10)
def loss(con, profile, v):
    l = v["loss"] / 100
    need = 1 / (1 - l) - 1
    a = v["asset"] if v["asset"] in ASSETS else "MSCIW"
    dds = service.drawdowns(con, a, top=4)
    text = [f"Nach −{fmt_num(v['loss'], 0)} % braucht es +{fmt_num(need * 100, 0)} %, um wieder beim alten Stand zu sein.",
            "Je größer der Verlust, desto schneller wächst der nötige Anstieg: −50 % brauchen +100 %, −80 % brauchen +400 %."]
    for d in dds[:1]:
        back = (f"nach {d['months_back']} Monaten wieder erreicht" if d["recovered"] else "bis heute nicht wieder erreicht")
        text.append(f"{ASSETS[a]['name']}: der größte Absturz war {fmt_num(d['depth'] * 100, 0)} % "
                    f"({pd.Timestamp(d['peak']).strftime('%m/%Y')} bis {pd.Timestamp(d['trough']).strftime('%m/%Y')}), "
                    f"{back}.")
    levels = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    return {
        "tiles": [{"label": "Nötiger Anstieg", "value": f"+{fmt_num(need * 100, 0)} %", "tone": "accent"}],
        "text": text,
        "charts": [{"type": "bar", "title": "Verlust → nötiger Anstieg", "unit": "pct",
                    "x": [f"−{x} %" for x in levels],
                    "series": [{"name": "nötiger Anstieg", "role": "depot",
                                "data": [round((1 / (1 - x / 100) - 1) * 100, 1) for x in levels]}]}],
        "table": {"head": ["Hoch", "Tief", "Absturz", "wieder erreicht"],
                  "rows": [[pd.Timestamp(d["peak"]).strftime("%m/%Y"), pd.Timestamp(d["trough"]).strftime("%m/%Y"),
                            f"{fmt_num(d['depth'] * 100, 0)} %",
                            f"nach {d['months_back']} Monaten" if d["recovered"] else "noch nicht"] for d in dds]},
    }


_ = markets  # Registry wird von den Feldern genutzt
