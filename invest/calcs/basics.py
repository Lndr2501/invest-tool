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
@calculator("compound", "Zinseszins: Wie wächst mein Geld?", "Wie viel habe ich später, wenn mein Geld jedes Jahr "
            "gleich stark wächst?", "build", [
                money_field("start", "Startbetrag", 1000,
                            "Geld, das du am Anfang auf einmal anlegst. 0, wenn du nur monatlich sparst."),
                money_field("monthly", "Sparrate pro Monat", 100,
                            "Was du jeden Monat dazu anlegst. Automatisch geht das mit einem Sparplan bei der Bank."),
                years_field("years", "Dauer in Jahren", 20),
                pct_field("rate", "Wertzuwachs pro Jahr", 5.0, "Um wie viel Prozent dein Geld pro Jahr im Schnitt "
                          "wächst (Rendite). Richtwerte: Tagesgeld (Sparkonto) 1–3 %, Aktien aus aller Welt langfristig "
                          "etwa 5–7 %, aber mit Schwankung.", lo=-10, hi=30),
                pct_field("dyn", "Sparrate jedes Jahr erhöhen", 0.0, "Wenn dein Gehalt steigt, kannst du mehr sparen. "
                          "Bei 2 % werden aus 100 € im Monat im nächsten Jahr 102 €. 0 = immer gleich viel.",
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
            f"Eingezahlt hast du {fmt_money(end['paid'], cur)}, der Rest von {fmt_money(gain, cur)} ist Wertzuwachs "
            f"({_pct(share, 0)} vom Endbetrag)."]
    if v["monthly"] and last_gain > v["monthly"] * 12:
        text.append(f"Im letzten Jahr bringt der Wertzuwachs ({fmt_money(last_gain, cur)}) mehr als deine Einzahlungen. "
                    "Das ist der Zinseszins: Gewinne bringen selbst wieder Gewinne.")
    text.append("Echte Anlagen schwanken, der feste Wertzuwachs ist nur ein Durchschnitt. Was in guten und schlechten "
                "Fällen herauskommen kann, zeigt „Was könnte daraus werden?“.")
    return {
        "tiles": [{"label": "Endbetrag", "value": fmt_money(end["value"], cur), "tone": "accent"},
                  {"label": "Eingezahlt", "value": fmt_money(end["paid"], cur)},
                  {"label": "Wertzuwachs", "value": fmt_money(gain, cur), "tone": "good" if gain >= 0 else "bad"}],
        "text": text,
        "charts": [{"type": "stack", "title": "Eingezahlt und Wertzuwachs je Jahr", "unit": "money",
                    "x": [f"{r['year']}" for r in rows],
                    "series": [{"name": "Eingezahlt", "role": "invest", "data": [round(r["paid"], 2) for r in rows]},
                               {"name": "Wertzuwachs", "role": "depot",
                                "data": [round(r["value"] - r["paid"], 2) for r in rows]}]}],
        "table": {"head": ["Jahr", "Eingezahlt", "Wert"],
                  "rows": [[r["year"], fmt_money(r["paid"], cur), fmt_money(r["value"], cur)] for r in rows]},
        "notes": ["Ohne Steuern und Inflation gerechnet. Einzahlung jeweils am Monatsanfang."],
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


@calculator("withdraw", "Wie lange reicht mein Geld?", "Ich nehme jeden Monat Geld heraus: Wie lange reicht es – "
            "oder wie viel kann ich nehmen?", "plan", [
                {"key": "mode", "label": "Was möchtest du wissen?", "type": "select", "default": "duration",
                 "options": [["duration", "Wie lange reicht mein Geld?"],
                             ["amount", "Wie viel kann ich monatlich nehmen?"]]},
                money_field("capital", "Vermögen zu Beginn", 100000, "Wie viel Geld angelegt ist, wenn du mit dem "
                            "Herausnehmen anfängst, z. B. zum Rentenbeginn."),
                money_field("withdraw", "Entnahme pro Monat", 600, "Wie viel du jeden Monat herausnimmst, um davon "
                            "zu leben.", show_if={"mode": "duration"}),
                years_field("years", "Über wie viele Jahre?", 25, "Wie lange das Geld reichen soll, z. B. von 67 bis 92 "
                            "sind 25 Jahre.", show_if={"mode": "amount"}),
                pct_field("rate", "Wertzuwachs pro Jahr", 4.0, "Um wie viel Prozent das übrige Geld pro Jahr im Schnitt "
                          "weiter wächst, während du herausnimmst. Vorsichtig: 3–4 %, nur Tagesgeld: 1–3 %.",
                          lo=-5, hi=15),
                pct_field("infl", "Inflationsausgleich pro Jahr", 2.0, "Preise steigen, deshalb nimmst du jedes Jahr "
                          "etwas mehr. Bei 2 % werden aus 1.000 € im Monat im nächsten Jahr 1.020 €. Richtwert: 2 %.",
                          lo=0, hi=10),
            ], icon="🏖️", order=30)
def withdraw(con, profile, v):
    cur = cur_of(profile)
    cap, rate, infl = v["capital"] or 0, v["rate"] / 100, v["infl"] / 100
    if cap <= 0:
        raise CalcError("Bitte trag ein, wie viel Geld du zu Beginn hast.")
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
        text = [f"Du kannst {int(n)} Jahre lang etwa {fmt_money(w, cur)} im Monat herausnehmen"
                + (f", jedes Jahr {_pct(infl)} mehr." if infl else ".") + " Danach ist das Geld aufgebraucht.",
                f"Insgesamt bekommst du {fmt_money(rows[-1]['total'], cur)} ausgezahlt."]
        if forever:
            text.append(f"Gibst du nur den Wertzuwachs aus und behältst das Vermögen, sind es etwa "
                        f"{fmt_money(forever, cur)} im Monat"
                        + (", dann aber ohne Ausgleich für steigende Preise." if infl else "."))
        tiles = [{"label": "Entnahme pro Monat", "value": fmt_money(w, cur), "tone": "accent"},
                 {"label": "Ausgezahlt gesamt", "value": fmt_money(rows[-1]["total"], cur)},
                 {"label": "Nur der Wertzuwachs", "value": fmt_money(forever, cur) if forever else "–"}]
    else:
        w = v["withdraw"] or 0
        if w <= 0:
            raise CalcError("Bitte trag ein, wie viel du pro Monat herausnehmen willst.")
        rows = _drain(cap, w, rate, infl)
        lasting = rows[-1]["value"] > 0
        years = len(rows)
        if lasting:
            text = [f"Dein Geld reicht praktisch für immer (über 100 Jahre): Der Wertzuwachs deckt deine "
                    f"{fmt_money(w, cur)} im Monat."]
        else:
            text = [f"Bei {fmt_money(w, cur)} im Monat reicht dein Geld etwa {years} Jahre"
                    + (f", wenn du jedes Jahr {_pct(infl)} mehr nimmst." if infl else ".")]
        text.append(f"Bis dahin bekommst du {fmt_money(rows[-1]['total'], cur)} ausgezahlt, also "
                    f"{fmt_money(max(rows[-1]['total'] - cap, 0), cur)} mehr als dein Startvermögen. Das Plus kommt "
                    "vom Wertzuwachs.")
        text.append("Faustregel: Nimm pro Jahr höchstens 3–4 % vom Startvermögen, bei 100.000 € also etwa 250–330 € "
                    "im Monat. Mit Aktien vieler Firmen hat das in der Vergangenheit meist 30 Jahre gereicht.")
        tiles = [{"label": "Reicht für", "value": "über 100 Jahre" if lasting else f"{years} Jahre", "tone": "accent"},
                 {"label": "Ausgezahlt gesamt", "value": fmt_money(rows[-1]["total"], cur)},
                 {"label": "Entnahme pro Jahr", "value": _pct(w * 12 / cap), "note": "vom Startvermögen"}]
    return {
        "tiles": tiles, "text": text,
        "charts": [{"type": "line", "title": "So viel Geld ist noch übrig", "unit": "money",
                    "x": [str(r["year"]) for r in rows],
                    "series": [{"name": "Restvermögen", "role": "depot", "data": [round(r["value"], 2) for r in rows]}]}],
        "table": {"head": ["Jahr", "Entnahme im Jahr", "Rest"],
                  "rows": [[r["year"], fmt_money(r["withdraw"], cur), fmt_money(r["value"], cur)] for r in rows[:60]]},
        "notes": ["Mit festem Wertzuwachs und ohne Steuern gerechnet. Echte Kurse schwanken: Schlechte Börsenjahre "
                  "gleich zu Beginn schaden mehr als später, weil dann weniger Geld übrig ist, das sich erholen kann."],
    }


# ---------- Inflation ----------
def _inflation_fields(profile):
    geo = (profile or {}).get("settings", {}).get("inflation_geo", "DE")
    return [
        {"key": "mode", "label": "Was möchtest du wissen?", "type": "select", "default": "future",
         "options": [["future", "Was ist mein Geld in Zukunft wert?"], ["past", "Was war mein Geld früher wert?"]]},
        money_field("amount", "Betrag", 1000),
        years_field("years", "In wie vielen Jahren?", 10, show_if={"mode": "future"}),
        pct_field("rate", "Inflation pro Jahr", 2.0, "Um wie viel Prozent die Preise pro Jahr steigen. Richtwert: 2 %, "
                  "das ist das Ziel der Europäischen Zentralbank. Leer lassen = Durchschnitt der letzten 10 Jahre.",
                  lo=0, hi=20, show_if={"mode": "future"}),
        {"key": "since", "label": "Aus dem Jahr", "type": "number", "default": 2000, "min": 1997, "max": 2026, "step": 1,
         "help": "Aus welchem Jahr stammt der Betrag? Zum Beispiel das Jahr, in dem du ihn gespart hast.",
         "show_if": {"mode": "past"}},
        {"key": "geo", "label": "Land", "type": "select", "default": geo,
         "options": [[k, v] for k, v in INFLATION_GEOS.items()]},
    ]


@calculator("inflation", "Inflation: Was ist mein Geld wert?", "Preise steigen: Was kann ich mir mit meinem Geld "
            "später noch kaufen – und was früher?", "past", _inflation_fields, icon="🎈", order=20)
def inflation(con, profile, v):
    cur = cur_of(profile)
    amount = v["amount"] or 0
    geo = v["geo"] or "DE"
    if v["mode"] == "past":
        h = db.load_hicp(con, geo)
        if h.empty:
            raise CalcError("Für dieses Land sind noch keine Preisdaten geladen.")
        since = int(v["since"] or 2000)
        hs = h[h.index.year >= since]
        if hs.empty:
            raise CalcError("Für dieses Jahr gibt es keine Daten.")
        ratio = float(h.iloc[-1] / hs.iloc[0])
        yrs = (h.index[-1] - hs.index[0]).n / 12
        pa = ratio ** (1 / yrs) - 1 if yrs > 0 else 0
        yearly = hs.groupby(hs.index.year).last()
        text = [f"Für das, was {fmt_money(amount, cur)} im Jahr {since} gekostet hat, zahlst du heute etwa "
                f"{fmt_money(amount * ratio, cur)}.",
                f"Die Preise sind seitdem um {_pct(ratio - 1, 0)} gestiegen, im Schnitt um {_pct(pa)} pro Jahr "
                f"({INFLATION_GEOS.get(geo, geo)}).",
                f"Hast du {fmt_money(amount, cur)} seit {since} ohne Zinsen liegen lassen, kaufst du davon heute nur "
                f"noch so viel wie damals mit {fmt_money(amount / ratio, cur)}."]
        x = [str(y) for y in yearly.index]
        data = [round(amount * float(h.iloc[-1] / yv), 2) for yv in yearly.values]
        return {"tiles": [{"label": "Heute nötig", "value": fmt_money(amount * ratio, cur), "tone": "accent"},
                          {"label": "Preisanstieg", "value": _pct(ratio - 1, 0)},
                          {"label": "Pro Jahr", "value": _pct(pa)}],
                "text": text,
                "charts": [{"type": "line", "title": f"Heute nötig für {fmt_money(amount, cur)} aus dem jeweiligen Jahr",
                            "unit": "money", "x": x, "series": [{"name": "heutiger Wert", "role": "depot", "data": data}]}],
                "notes": ["Quelle: Verbraucherpreise der EU-Statistikbehörde Eurostat (HVPI), monatlich."]}
    rate = v["rate"]
    if rate is None:
        rate = service._infl_pa(con, geo) * 100
    r = rate / 100
    years = int(v["years"])
    rows = [{"year": y, "real": amount / (1 + r) ** y, "needed": amount * (1 + r) ** y} for y in range(0, years + 1)]
    end = rows[-1]
    return {
        "tiles": [{"label": f"In {years} Jahren noch wert", "value": fmt_money(end["real"], cur), "tone": "bad"},
                  {"label": "Nötig für gleiche Einkäufe", "value": fmt_money(end["needed"], cur)},
                  {"label": "Wertverlust", "value": _pct(1 - end["real"] / amount if amount else 0, 0)}],
        "text": [f"Bei {_pct(r)} Inflation kannst du dir in {years} Jahren mit {fmt_money(amount, cur)} nur noch so viel "
                 f"kaufen wie heute mit {fmt_money(end['real'], cur)}.",
                 f"Damit du dir dann genauso viel kaufen kannst, müsste dein Geld auf {fmt_money(end['needed'], cur)} "
                 f"wachsen. Es muss also nach Steuern mindestens {_pct(r)} pro Jahr bringen."],
        "charts": [{"type": "line", "title": "Was dein Geld noch wert ist", "unit": "money",
                    "x": [str(x["year"]) for x in rows],
                    "series": [{"name": "Wert in heutigem Geld", "role": "depot", "data": [round(x["real"], 2) for x in rows]},
                               {"name": "Nötig für gleiche Einkäufe", "role": "invest",
                                "data": [round(x["needed"], 2) for x in rows]}]}],
    }


# ---------- Kostenvergleich ----------
@calculator("costs", "Was kosten mich Gebühren?", "Günstiger ETF oder teurer Fonds: Wie viel Geld kosten mich die "
            "Gebühren über die Jahre?", "cost", [
                money_field("start", "Startbetrag", 5000,
                            "Geld, das du am Anfang auf einmal anlegst. 0, wenn du nur monatlich sparst."),
                money_field("monthly", "Sparrate pro Monat", 200, "Was du jeden Monat dazu anlegst."),
                years_field("years", "Dauer in Jahren", 25),
                pct_field("rate", "Wertzuwachs vor Kosten", 6.0, "Um wie viel Prozent die Anlage pro Jahr im Schnitt "
                          "wächst, bevor Gebühren abgehen. Gilt für A und B gleich. Welt-Aktien langfristig etwa 5–7 %.",
                          lo=-5, hi=20),
                pct_field("cost_a", "Laufende Kosten A", 0.2, "A ist z. B. ein ETF, also ein günstiger Korb mit vielen "
                          "Aktien. Er zieht jedes Jahr 0,1–0,3 % vom Geld ab.", lo=0, hi=5, step=0.01),
                pct_field("front_a", "Ausgabeaufschlag A", 0.0, "Einmalige Gebühr auf jede Einzahlung. Bei ETFs meist "
                          "0 %.", lo=0, hi=10),
                pct_field("cost_b", "Laufende Kosten B", 1.8, "B ist z. B. ein Bank-Fonds, bei dem ein Manager die "
                          "Aktien auswählt, oder eine Rentenversicherung mit Fonds. Oft 1,5–2,5 % pro Jahr.",
                          lo=0, hi=5, step=0.01),
                pct_field("front_b", "Ausgabeaufschlag B", 5.0, "Einmalige Gebühr auf jede Einzahlung. Bei Bank-Fonds "
                          "oft 5 %: Von 100 € werden nur 95 € angelegt.", lo=0, hi=10),
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
                 f"Die {fmt_money(diff, cur)} Unterschied gehen allein für Gebühren drauf. Das sind "
                 f"{_pct(diff / paid if paid else 0, 0)} von dem, was du eingezahlt hast.",
                 "Ein teurer Fonds muss also jedes Jahr deutlich mehr schaffen als ein günstiger ETF, nur um "
                 "gleichzuziehen. Über viele Jahre gelingt das nur wenigen."],
        "charts": [{"type": "line", "title": "Vermögen mit A und B", "unit": "money", "x": [str(r["year"]) for r in a],
                    "series": [{"name": "A (günstig)", "role": "depot", "data": [round(r["value"], 2) for r in a]},
                               {"name": "B (teuer)", "role": "c2", "data": [round(r["value"], 2) for r in b]},
                               {"name": "Eingezahlt", "role": "invest", "data": [round(r["paid"], 2) for r in a]}]}],
        "notes": ["A und B mit gleichem Wertzuwachs vor Kosten gerechnet, ohne Steuern."],
    }


# ---------- Verlust aufholen ----------
def _loss_fields(profile):
    opts = [[a, ASSETS[a]["name"]] for a in ASSETS if ASSETS[a]["kind"] in ("etf", "metal", "crypto", "stock")]
    return [pct_field("loss", "Verlust", 30.0, "Um wie viel Prozent deine Anlage gefallen ist. Beispiel: Aus 1.000 € "
                      "wurden 700 €, das sind 30 % Verlust.", lo=1, hi=99, step=1),
            {"key": "asset", "label": "Echte Abstürze ansehen von", "type": "market", "default": "MSCIW",
             "options": opts}]


@calculator("loss", "Wie viel muss es wieder steigen?", "Nach einem Verlust: Wie viel Plus brauche ich bis zum alten "
            "Stand – und wie lange dauerte das früher?", "learn", _loss_fields, icon="🧗", order=10)
def loss(con, profile, v):
    l = v["loss"] / 100
    need = 1 / (1 - l) - 1
    a = v["asset"] if v["asset"] in ASSETS else "MSCIW"
    dds = service.drawdowns(con, a, top=4)
    text = [f"Fällt deine Anlage um {fmt_num(v['loss'], 0)} %, muss sie danach um {fmt_num(need * 100, 0)} % steigen, "
            "bis du wieder beim alten Stand bist.",
            "Je tiefer der Absturz, desto stärker muss es danach steigen: Nach −50 % braucht es +100 %, nach −80 % "
            "sogar +400 %."]
    for d in dds[:1]:
        back = (f"nach {d['months_back']} Monaten wieder erreicht" if d["recovered"] else "bis heute nicht wieder erreicht")
        text.append(f"{ASSETS[a]['name']}: Der größte Absturz vom Hoch war {fmt_num(d['depth'] * 100, 0)} % "
                    f"({pd.Timestamp(d['peak']).strftime('%m/%Y')} bis {pd.Timestamp(d['trough']).strftime('%m/%Y')}). "
                    f"Der alte Stand wurde {back}.")
    levels = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    return {
        "tiles": [{"label": "Nötiger Anstieg", "value": f"+{fmt_num(need * 100, 0)} %", "tone": "accent"}],
        "text": text,
        "charts": [{"type": "bar", "title": "Verlust → nötiger Anstieg", "unit": "pct",
                    "x": [f"−{x} %" for x in levels],
                    "series": [{"name": "nötiger Anstieg", "role": "depot",
                                "data": [round((1 / (1 - x / 100) - 1) * 100, 1) for x in levels]}]}],
        "table": {"head": ["Hoch", "Tief", "Absturz", "Alter Stand"],
                  "rows": [[pd.Timestamp(d["peak"]).strftime("%m/%Y"), pd.Timestamp(d["trough"]).strftime("%m/%Y"),
                            f"{fmt_num(d['depth'] * 100, 0)} %",
                            f"nach {d['months_back']} Monaten" if d["recovered"] else "noch nicht"] for d in dds]},
    }


_ = markets  # Registry wird von den Feldern genutzt
