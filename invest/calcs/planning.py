"""Planungs-Rechner: Sparrate aus dem Einkommen (mit Notgroschen) und Rentenlücke."""
from __future__ import annotations

from ..units import fmt_money, fmt_num
from . import CalcError, calculator, cur_of, money_field, pct_field, years_field
from .basics import _final, grow


def _fin(profile, key, default):
    f = (profile or {}).get("settings", {}).get("finance") or {}
    v = f.get(key)
    return default if v in (None, "") else v


def _budget_fields(profile):
    return [
        money_field("income", "Netto-Einkommen pro Monat", _fin(profile, "income", 2500),
                    "Was jeden Monat nach Steuern und Abgaben auf deinem Konto ankommt, auch Kindergeld."),
        money_field("fixed", "Feste Kosten pro Monat", _fin(profile, "expenses", 1200),
                    "Was jeden Monat fest abgeht: Miete, Strom, Versicherungen, Handy, Abos, Kredite, Auto."),
        money_field("living", "Lebenshaltung pro Monat", 600, "Was du im Alltag ausgibst: Essen, Drogerie, Kleidung, "
                    "Freizeit, Ausgehen. Grob schätzen reicht."),
        money_field("cash", "Erspartes auf dem Konto", _fin(profile, "cash", 1000),
                    "Geld, an das du jederzeit kommst: Girokonto und Tagesgeld (Sparkonto mit Zinsen, täglich "
                    "verfügbar)."),
        money_field("debt", "Teure Schulden", _fin(profile, "debt", 0),
                    "Dispo (Minus auf dem Girokonto), Ratenkredite, Kreditkarte. Nicht gemeint ist ein Kredit fürs "
                    "Haus. Keine Schulden: 0."),
    ]


@calculator("budget", "Wie viel kann ich sparen?", "Was bleibt jeden Monat übrig, wie viel Notgroschen brauche ich, "
            "und womit fange ich an?", "plan", _budget_fields,
            icon="🧮", order=10)
def budget(con, profile, v):
    cur = cur_of(profile)
    inc, fixed, living = v["income"] or 0, v["fixed"] or 0, v["living"] or 0
    if inc <= 0:
        raise CalcError("Bitte trag dein Netto-Einkommen ein.")
    free = inc - fixed - living
    target_share = 0.2 * inc
    suggest = max(min(free, target_share), 0) if free < target_share * 1.5 else free * 0.7
    expenses = fixed + living
    emergency = 3 * expenses
    gap = max(emergency - (v["cash"] or 0), 0)
    steps, text = [], []
    if free <= 0:
        text.append(f"Deine Ausgaben ({fmt_money(expenses, cur)}) sind so hoch wie dein Einkommen oder höher. "
                    "Bevor du Geld anlegst, lohnt ein Blick auf feste Kosten wie Verträge, Abos und Versicherungen.")
    else:
        text.append(f"Nach allen Ausgaben bleiben dir etwa {fmt_money(free, cur)} im Monat "
                    f"({fmt_num(free / inc * 100, 0)} % deines Einkommens).")
        text.append(f"Faustregel: Spar rund 20 % von deinem Netto, bei dir {fmt_money(target_share, cur)}. "
                    f"Vorschlag für dich: {fmt_money(suggest, cur)} im Monat. Den Rest behältst du als Puffer.")
    if v["debt"]:
        steps.append(f"1. Zuerst teure Schulden abbezahlen ({fmt_money(v['debt'], cur)}). Ein Dispo kostet oft 10–15 % "
                     "Zinsen im Jahr, mehr als jede Anlage sicher bringt.")
    if gap > 0:
        months = gap / suggest if suggest > 0 else None
        steps.append(f"{len(steps) + 1}. Notgroschen von {fmt_money(emergency, cur)} aufbauen: 3 Monatsausgaben auf dem "
                     f"Tagesgeld, für Notfälle wie ein kaputtes Auto. Dir fehlen noch {fmt_money(gap, cur)}"
                     + (f", mit dem Vorschlag oben etwa {fmt_num(months, 0)} Monate." if months else "."))
    steps.append(f"{len(steps) + 1}. Danach regelmäßig anlegen. Geld für die nächsten 3 Jahre bleibt auf dem "
                 "Tagesgeld. Was 10 Jahre und länger liegen kann, kommt in einen Aktien-ETF: einen Korb mit Aktien "
                 "vieler Firmen aus aller Welt.")
    text += steps
    share = lambda x: round(x / inc * 100, 1)  # noqa: E731
    return {
        "tiles": [{"label": "Bleibt im Monat", "value": fmt_money(free, cur), "tone": "good" if free > 0 else "bad"},
                  {"label": "Vorschlag Sparrate", "value": fmt_money(suggest, cur), "tone": "accent"},
                  {"label": "Notgroschen-Ziel", "value": fmt_money(emergency, cur),
                   "note": "erreicht" if gap <= 0 else f"es fehlen {fmt_money(gap, cur)}"}],
        "text": text,
        "charts": [{"type": "hbar", "title": "Wohin dein Einkommen geht (in % vom Netto)", "unit": "pct",
                    "x": ["Feste Kosten", "Lebenshaltung", "Sparen (Vorschlag)", "Puffer"],
                    "series": [{"name": "Anteil", "role": "depot",
                                "data": [share(fixed), share(living), share(suggest), share(max(free - suggest, 0))]}],
                    "note": "Richtwert 50/30/20: höchstens 50 % feste Kosten, 30 % für Leben und Wünsche, 20 % sparen."}],
        "actions": [{"label": "In mein Finanzprofil übernehmen", "type": "save_finance",
                     "data": {"income": inc, "expenses": expenses, "cash": v["cash"] or 0, "debt": v["debt"] or 0,
                              "saving": round(suggest, 0)}}],
        "notes": ["Richtwerte, keine Anlageberatung. Notgroschen: 3 Monatsausgaben, für Selbstständige eher 6."],
    }


def _ret_fields(profile):
    age = _fin(profile, "age", 30)
    return [
        {"key": "age", "label": "Dein Alter", "type": "number", "default": age, "min": 16, "max": 80, "step": 1},
        {"key": "retire", "label": "Rente mit", "type": "number", "default": 67, "min": 50, "max": 75, "step": 1,
         "help": "Mit welchem Alter du in Rente gehen willst. Regulär ist das für die meisten 67."},
        money_field("want", "Wunsch-Einkommen im Alter", 2000, "Wie viel du im Ruhestand pro Monat zum Leben haben "
                    "willst, in heutigem Geld (also zu heutigen Preisen). Oft sind das 70–80 % vom jetzigen Netto."),
        money_field("pension", "Erwartete Rente (netto)", 1200,
                    "Deine gesetzliche Rente in heutigem Geld. Sie steht in der Renteninformation, die du ab 27 jedes "
                    "Jahr per Post bekommst. Netto bleiben davon etwa 80–85 %."),
        money_field("have", "Schon fürs Alter angespart", 0,
                    "Geld, das du schon für den Ruhestand zurückgelegt hast, z. B. in Aktien oder auf dem Tagesgeld."),
        pct_field("rate", "Wertzuwachs bis zur Rente", 5.0, "Um wie viel Prozent dein Geld pro Jahr im Schnitt wächst "
                  "(Rendite). Aktien aus aller Welt langfristig etwa 5–7 % vor Inflation, Tagesgeld 1–3 %.", lo=0, hi=12),
        pct_field("infl", "Inflation pro Jahr", 2.0, "Um wie viel Prozent die Preise pro Jahr steigen. Bei 2 % kaufen "
                  "100 € in 10 Jahren nur noch so viel wie heute 82 €. Richtwert: 2 %.", lo=0, hi=8),
        years_field("payout_years", "Wie lange soll das Geld reichen?", 25, "Jahre ab Rentenbeginn. Mit 67 in Rente "
                    "und 25 Jahre: Das Geld reicht bis 92.", lo=5, hi=40),
    ]


@calculator("retirement", "Rentenlücke: Wie viel fehlt mir?", "Was fehlt mir neben der gesetzlichen Rente – und "
            "wie viel muss ich dafür monatlich anlegen?", "plan", _ret_fields, icon="👵", order=20)
def retirement(con, profile, v):
    cur = cur_of(profile)
    years = int(v["retire"] - v["age"])
    if years <= 0:
        raise CalcError("Das Rentenalter muss höher sein als dein jetziges Alter.")
    r, infl = v["rate"] / 100, v["infl"] / 100
    gap_today = max((v["want"] or 0) - (v["pension"] or 0), 0)
    if gap_today <= 0:
        return {"tiles": [{"label": "Rentenlücke", "value": fmt_money(0, cur), "tone": "good"}],
                "text": ["Nach deinen Angaben reicht die gesetzliche Rente für dein Wunsch-Einkommen. Zusätzliches Sparen "
                         "gibt dir trotzdem mehr Spielraum."]}
    gap_then = gap_today * (1 + infl) ** years
    # Kapital zum Rentenbeginn: Entnahme steigt mit Inflation, Rendite in der Entnahme vorsichtiger (3 %)
    lo, hi = 0.0, gap_then * 12 * v["payout_years"] * 2
    for _ in range(80):
        mid = (lo + hi) / 2
        if _final(mid, gap_then, 0.03, infl, int(v["payout_years"])) < 0:
            lo = mid
        else:
            hi = mid
    capital = hi
    have_then = (v["have"] or 0) * (1 + r) ** years
    need = max(capital - have_then, 0)
    i = (1 + r) ** (1 / 12) - 1
    n = years * 12
    monthly = need * i / (((1 + i) ** n - 1) * (1 + i)) if i else need / n
    rows = grow(v["have"] or 0, monthly, years, r)
    text = [f"Dir fehlen im Ruhestand etwa {fmt_money(gap_today, cur)} im Monat, in heutigem Geld. Weil die Preise "
            f"steigen, sind das in {years} Jahren {fmt_money(gap_then, cur)}.",
            f"Um diese Lücke {int(v['payout_years'])} Jahre lang zu füllen, brauchst du zum Rentenbeginn etwa "
            f"{fmt_money(capital, cur)} angespart.",
            f"Dafür müsstest du ab jetzt rund {fmt_money(monthly, cur)} im Monat anlegen "
            f"(bei {fmt_num(v['rate'], 1)} % Wertzuwachs pro Jahr).",
            "Je früher du anfängst, desto mehr erledigt der Zinseszins: Gewinne bringen selbst wieder Gewinne. "
            "Wer 10 Jahre später startet, muss meist fast doppelt so viel pro Monat sparen."]
    return {
        "tiles": [{"label": "Lücke pro Monat", "value": fmt_money(gap_today, cur), "note": "in heutigem Geld"},
                  {"label": "Nötig zum Rentenbeginn", "value": fmt_money(capital, cur)},
                  {"label": "Sparrate pro Monat", "value": fmt_money(monthly, cur), "tone": "accent"}],
        "text": text,
        "charts": [{"type": "stack", "title": "Aufbau bis zur Rente", "unit": "money",
                    "x": [str(int(v["age"]) + r_["year"]) for r_ in rows],
                    "series": [{"name": "Eingezahlt", "role": "invest", "data": [round(x["paid"], 2) for x in rows]},
                               {"name": "Wertzuwachs", "role": "depot", "data": [round(x["value"] - x["paid"], 2) for x in rows]}]}],
        "actions": [{"label": "Mit echten Kursen prüfen", "type": "link",
                     "href": f"#/rechner/future?initial={round(v['have'] or 0)}&monthly={round(monthly)}&years={min(years, 40)}"}],
        "notes": ["Ohne Steuern gerechnet. Im Ruhestand mit 3 % Wertzuwachs pro Jahr und Auszahlungen, die jedes Jahr "
                  "mit der Inflation steigen."],
    }
