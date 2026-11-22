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
                    "Was nach Steuern und Sozialabgaben auf dem Konto ankommt (inkl. Kindergeld o. Ä.)."),
        money_field("fixed", "Feste Kosten pro Monat", _fin(profile, "expenses", 1200),
                    "Miete, Strom, Versicherungen, Handy, Abos, Kredite, Auto."),
        money_field("living", "Lebenshaltung pro Monat", 600, "Essen, Drogerie, Kleidung, Freizeit, Ausgehen."),
        money_field("cash", "Erspartes auf Konto/Tagesgeld", _fin(profile, "cash", 1000)),
        money_field("debt", "Teure Schulden (Dispo, Ratenkredit)", _fin(profile, "debt", 0)),
    ]


@calculator("budget", "Wie viel kann ich sparen?", "Aus Einkommen und Ausgaben: Was ist eine sinnvolle Sparrate, "
            "wie groß sollte der Notgroschen sein und in welcher Reihenfolge geht man vor?", "plan", _budget_fields,
            icon="🧮", order=10)
def budget(con, profile, v):
    cur = cur_of(profile)
    inc, fixed, living = v["income"] or 0, v["fixed"] or 0, v["living"] or 0
    if inc <= 0:
        raise CalcError("Bitte dein Netto-Einkommen eingeben.")
    free = inc - fixed - living
    target_share = 0.2 * inc
    suggest = max(min(free, target_share), 0) if free < target_share * 1.5 else free * 0.7
    expenses = fixed + living
    emergency = 3 * expenses
    gap = max(emergency - (v["cash"] or 0), 0)
    steps, text = [], []
    if free <= 0:
        text.append(f"Deine Ausgaben ({fmt_money(expenses, cur)}) sind so hoch wie oder höher als dein Einkommen. "
                    "Bevor du anlegst, lohnt ein Blick auf die festen Kosten (Verträge, Abos, Versicherungen).")
    else:
        text.append(f"Nach allen Ausgaben bleiben dir etwa {fmt_money(free, cur)} im Monat "
                    f"({fmt_num(free / inc * 100, 0)} % deines Einkommens).")
        text.append(f"Eine gute Faustregel: rund 20 % des Netto-Einkommens sparen ({fmt_money(target_share, cur)}). "
                    f"Vorschlag für dich: {fmt_money(suggest, cur)} im Monat – den Rest als Puffer behalten.")
    if v["debt"]:
        steps.append(f"1. Teure Schulden tilgen ({fmt_money(v['debt'], cur)}). Dispo kostet oft 10–15 % Zinsen – "
                     "mehr als jede Anlage sicher bringt.")
    if gap > 0:
        months = gap / suggest if suggest > 0 else None
        steps.append(f"{len(steps) + 1}. Notgroschen auf {fmt_money(emergency, cur)} bringen (3 Monatsausgaben, "
                     f"auf Tagesgeld). Dir fehlen {fmt_money(gap, cur)}"
                     + (f" – mit deiner Sparrate etwa {fmt_num(months, 0)} Monate." if months else "."))
    steps.append(f"{len(steps) + 1}. Danach regelmäßig anlegen: Was du in den nächsten 3 Jahren brauchst, aufs "
                 "Tagesgeld; was 10 Jahre und länger liegen kann, breit gestreut in Aktien-ETFs.")
    text += steps
    share = lambda x: round(x / inc * 100, 1)  # noqa: E731
    return {
        "tiles": [{"label": "Bleibt im Monat", "value": fmt_money(free, cur), "tone": "good" if free > 0 else "bad"},
                  {"label": "Vorschlag Sparrate", "value": fmt_money(suggest, cur), "tone": "accent"},
                  {"label": "Notgroschen-Ziel", "value": fmt_money(emergency, cur),
                   "note": "erreicht" if gap <= 0 else f"fehlen {fmt_money(gap, cur)}"}],
        "text": text,
        "charts": [{"type": "hbar", "title": "Wohin dein Einkommen geht (in % vom Netto)", "unit": "pct",
                    "x": ["Feste Kosten", "Lebenshaltung", "Sparen (Vorschlag)", "Puffer"],
                    "series": [{"name": "Anteil", "role": "depot",
                                "data": [share(fixed), share(living), share(suggest), share(max(free - suggest, 0))]}],
                    "note": "Richtwert 50/30/20: höchstens 50 % feste Kosten, 30 % für Leben und Wünsche, 20 % sparen."}],
        "actions": [{"label": "In mein Finanzprofil übernehmen", "type": "save_finance",
                     "data": {"income": inc, "expenses": expenses, "cash": v["cash"] or 0, "debt": v["debt"] or 0,
                              "saving": round(suggest, 0)}}],
        "notes": ["Richtwerte, keine Anlageberatung. Notgroschen: 3 Monatsausgaben, Selbstständige eher 6."],
    }


def _ret_fields(profile):
    age = _fin(profile, "age", 30)
    return [
        {"key": "age", "label": "Dein Alter", "type": "number", "default": age, "min": 16, "max": 80, "step": 1},
        {"key": "retire", "label": "Rente ab", "type": "number", "default": 67, "min": 50, "max": 75, "step": 1},
        money_field("want", "Gewünschtes Netto im Ruhestand (heutige Kaufkraft)", 2000),
        money_field("pension", "Erwartete gesetzliche Rente netto (heutige Kaufkraft)", 1200,
                    "Steht in deiner Renteninformation (ab 27 jährlich per Post). Netto ≈ 80–85 % davon."),
        money_field("have", "Schon für das Alter angespart", 0),
        pct_field("rate", "Rendite pro Jahr bis zur Rente", 5.0, "Welt-Aktien-ETF langfristig etwa 5–7 % vor Inflation.",
                  lo=0, hi=12),
        pct_field("infl", "Inflation pro Jahr", 2.0, lo=0, hi=8),
        years_field("payout_years", "Wie lange soll das Geld reichen?", 25, lo=5, hi=40),
    ]


@calculator("retirement", "Wie groß ist meine Rentenlücke?", "Wie viel fehlt im Alter – und wie viel muss ich dafür "
            "monatlich anlegen?", "plan", _ret_fields, icon="👵", order=20)
def retirement(con, profile, v):
    cur = cur_of(profile)
    years = int(v["retire"] - v["age"])
    if years <= 0:
        raise CalcError("Das Rentenalter muss über deinem Alter liegen.")
    r, infl = v["rate"] / 100, v["infl"] / 100
    gap_today = max((v["want"] or 0) - (v["pension"] or 0), 0)
    if gap_today <= 0:
        return {"tiles": [{"label": "Rentenlücke", "value": fmt_money(0, cur), "tone": "good"}],
                "text": ["Nach deinen Angaben deckt die gesetzliche Rente deinen Wunsch. Zusätzliches Sparen gibt dir "
                         "trotzdem Spielraum."]}
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
    text = [f"Dir fehlen im Ruhestand etwa {fmt_money(gap_today, cur)} im Monat (heutige Kaufkraft) – in {years} Jahren "
            f"sind das wegen der Inflation {fmt_money(gap_then, cur)}.",
            f"Damit das {int(v['payout_years'])} Jahre reicht, brauchst du zum Rentenbeginn etwa "
            f"{fmt_money(capital, cur)}.",
            f"Dafür solltest du ab jetzt rund {fmt_money(monthly, cur)} im Monat anlegen "
            f"(bei {fmt_num(v['rate'], 1)} % Rendite pro Jahr).",
            "Je früher du anfängst, desto mehr erledigt der Zinseszins: 10 Jahre später starten heißt meist fast "
            "doppelt so viel pro Monat."]
    return {
        "tiles": [{"label": "Lücke pro Monat (heute)", "value": fmt_money(gap_today, cur)},
                  {"label": "Nötiges Kapital", "value": fmt_money(capital, cur)},
                  {"label": "Sparrate pro Monat", "value": fmt_money(monthly, cur), "tone": "accent"}],
        "text": text,
        "charts": [{"type": "stack", "title": "Aufbau bis zur Rente", "unit": "money",
                    "x": [str(int(v["age"]) + r_["year"]) for r_ in rows],
                    "series": [{"name": "Eingezahlt", "role": "invest", "data": [round(x["paid"], 2) for x in rows]},
                               {"name": "Erträge", "role": "depot", "data": [round(x["value"] - x["paid"], 2) for x in rows]}]}],
        "actions": [{"label": "Mit echten Kursen prüfen", "type": "link",
                     "href": f"#/rechner/future?initial={round(v['have'] or 0)}&monthly={round(monthly)}&years={min(years, 40)}"}],
        "notes": ["Vor Steuern. Entnahme im Ruhestand mit 3 % Rendite und jährlichem Inflationsausgleich gerechnet."],
    }
