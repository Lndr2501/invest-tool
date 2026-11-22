"""Steuer-Rechner (Deutschland 2026, vereinfacht): eigener Steuersatz und Steuer auf Anlagegewinne."""
from __future__ import annotations

from .. import taxde
from ..units import fmt_money, fmt_num
from . import CalcError, calculator, cur_of, money_field

CHURCH = {"key": "church", "label": "Kirchensteuer", "type": "select", "default": "none",
          "options": [["none", "keine"], ["8", "8 % (Bayern, Baden-Württemberg)"], ["9", "9 % (andere Bundesländer)"]],
          "help": "Zahlst du nur, wenn du Mitglied einer Kirche bist. Sie kommt als Aufschlag auf deine Steuer dazu."}


def _fin(profile, key, default):
    f = (profile or {}).get("settings", {}).get("finance") or {}
    v = f.get(key)
    return default if v in (None, "") else v


def estimate_zve(gross: float) -> float:
    """Sehr grobe Schätzung für Angestellte: Brutto minus absetzbare Sozialabgaben (≈ 20 % bis zur
    Beitragsbemessungsgrenze der Krankenversicherung, darüber nur Rente/Arbeitslosen ≈ 10,6 % bis 101.400 €)
    minus Arbeitnehmer-Pauschbetrag 1.230 €."""
    kv_limit, rv_limit = 69_750, 101_400
    social = 0.20 * min(gross, kv_limit) + 0.106 * max(min(gross, rv_limit) - kv_limit, 0)
    return max(gross - social - 1_230, 0)


def _rate_fields(profile):
    zve = _fin(profile, "zve", None)
    return [
        {"key": "mode", "label": "Was weißt du?", "type": "select", "default": "zve" if zve else "gross",
         "options": [["gross", "Mein Bruttogehalt im Jahr (grobe Schätzung)"],
                     ["zve", "Mein zu versteuerndes Einkommen (genauer)"]],
         "help": "Das Bruttogehalt steht auf der Lohnabrechnung. Das zu versteuernde Einkommen steht im "
                 "Steuerbescheid und ergibt ein genaueres Ergebnis."},
        money_field("gross", "Bruttogehalt im Jahr", 45000, "Dein Lohn vor allen Abzügen. Die Abrechnung für Dezember "
                    "zeigt die Summe fürs ganze Jahr.", show_if={"mode": "gross"}),
        money_field("zve", "Zu versteuerndes Einkommen", zve or 36000,
                    "Dein Jahreseinkommen nach allen Abzügen, auf das die Steuer berechnet wird. Steht im letzten "
                    "Steuerbescheid.", show_if={"mode": "zve"}),
        {"key": "married", "label": "Verheiratet, gemeinsame Steuererklärung", "type": "toggle",
         "default": bool(_fin(profile, "married", False))},
        {**CHURCH, "default": str(_fin(profile, "church", "none"))},
    ]


@calculator("taxrate", "Welchen Steuersatz habe ich?", "Wie viel Einkommensteuer zahle ich – und was gilt für "
            "Gewinne aus Aktien, ETFs, Gold und Krypto?", "cost", _rate_fields, icon="🧾", order=10)
def taxrate(con, profile, v):
    cur = cur_of(profile)
    zve = estimate_zve(v["gross"] or 0) if v["mode"] == "gross" else (v["zve"] or 0)
    married = bool(v["married"])
    church = taxde.church_rate(v["church"])
    r = taxde.rates(zve, married, church)
    cap = taxde.capital_rate(church)
    allowance = 2000 if married else 1000
    text = []
    if v["mode"] == "gross":
        text.append(f"Dein zu versteuerndes Einkommen liegt grob geschätzt bei {fmt_money(zve, cur)}. Das ist dein "
                    "Brutto minus Sozialabgaben und einer Pauschale für Arbeitskosten.")
    text += [
        f"Im Schnitt zahlst du {fmt_num(r['average'] * 100, 1)} % Einkommensteuer"
        + (" inklusive Solidaritätszuschlag bzw. Kirchensteuer" if church or r["tax"] > 20_350 else "")
        + f", das sind {fmt_money(r['tax'], cur)} im Jahr.",
        f"Von jedem Euro, den du zusätzlich verdienst, gehen {fmt_num(r['marginal'] * 100, 1)} % an Steuer. "
        f"Das ist dein Grenzsteuersatz.",
        f"Gold, Silber, Krypto: Verkaufst du innerhalb eines Jahres mit Gewinn, zahlst du deinen Grenzsteuersatz "
        f"({fmt_num(r['marginal'] * 100, 0)} %). Das gilt ab 1.000 € Gewinn im Jahr, dann aber auf den ganzen Gewinn. "
        f"Nach mehr als einem Jahr ist der Gewinn steuerfrei.",
        f"Aktien, ETFs, Fonds: Auf Gewinne gilt immer die Abgeltungsteuer von {fmt_num(cap * 100, 2)} %, egal wie "
        f"viel du verdienst. Die ersten {fmt_money(allowance, cur)} Gewinn im Jahr bleiben steuerfrei (Freibetrag), wenn du "
        f"bei deiner Bank einen Freistellungsauftrag stellst. Bei Aktien-ETFs sind zusätzlich 30 % des Gewinns "
        f"steuerfrei.",
    ]
    if r["marginal"] < 0.25:
        text.append("Dein Grenzsteuersatz liegt unter 25 %. Beantrage in der Steuererklärung die Günstigerprüfung "
                    "(ein Kreuz in der Anlage KAP), dann bekommst du einen Teil der Abgeltungsteuer zurück.")
    xs = list(range(0, 150_001, 5_000))
    curve = [taxde.rates(x, married, church) for x in xs]
    return {
        "tiles": [{"label": "Grenzsteuersatz", "value": f"{fmt_num(r['marginal'] * 100, 1)} %", "tone": "accent",
                   "note": "auf jeden Euro mehr"},
                  {"label": "Durchschnittssteuersatz", "value": f"{fmt_num(r['average'] * 100, 1)} %",
                   "note": "auf dein ganzes Einkommen"},
                  {"label": "Steuer im Jahr", "value": fmt_money(r["tax"], cur)}],
        "text": text,
        "charts": [{"type": "line", "title": "Steuersatz nach Einkommen", "unit": "pct",
                    "x": [f"{x // 1000} Tsd." for x in xs],
                    "series": [{"name": "Grenzsteuersatz", "role": "depot", "data": [round(c["marginal"] * 100, 1) for c in curve]},
                               {"name": "Durchschnitt", "role": "invest", "data": [round(c["average"] * 100, 1) for c in curve]}],
                    "note": f"Dein zu versteuerndes Einkommen: {fmt_money(zve, cur)}"}],
        "actions": [{"label": "Als meinen Steuersatz speichern", "type": "save_finance",
                     "data": {"zve": round(zve), "married": married, "church": v["church"]}}],
        "notes": ["Gerechnet mit dem Steuertarif 2026 (§ 32a Einkommensteuergesetz). Solidaritätszuschlag erst ab "
                  "einer Einkommensgrenze, Kirchensteuer vereinfacht. Keine Steuerberatung."],
    }


@calculator("gaintax", "Wie viel Steuer kostet mein Gewinn?", "Ich verkaufe mit Gewinn: Was bleibt nach "
            "Steuern – je nach Anlage und wie lange ich sie hatte?", "cost", lambda profile: [
                {"key": "kind", "label": "Was hast du verkauft?", "type": "select", "default": "etf",
                 "options": [["etf", "Aktien-ETF oder Aktienfonds"], ["bond", "Anleihen-ETF oder anderer Fonds"],
                             ["stock", "Einzelaktie"], ["private", "Gold, Silber, Krypto (direkt gekauft)"]],
                 "help": "Bei Aktien-ETFs sind 30 % des Gewinns steuerfrei. Gold, Silber und Krypto, die du selbst "
                         "besitzt, sind nach mehr als einem Jahr ganz steuerfrei."},
                money_field("gain", "Gewinn", 5000, "Verkaufspreis minus Kaufpreis. Beispiel: für 10.000 € gekauft, für "
                            "15.000 € verkauft, das sind 5.000 € Gewinn."),
                {"key": "long", "label": "Länger als 1 Jahr gehalten", "type": "toggle", "default": True,
                 "show_if": {"kind": "private"}},
                money_field("used", "Freibetrag schon genutzt", 0, "Pro Jahr bleiben 1.000 € Gewinn steuerfrei, für Paare "
                            "2.000 € (Freibetrag, Fachwort: Sparerpauschbetrag). Trag ein, wie viel davon dieses Jahr "
                            "schon verbraucht ist, z. B. durch Zinsen. Sonst 0.", show_if={"kind": "etf"}),
                {"key": "rate", "label": "Dein Grenzsteuersatz", "type": "percent",
                 "default": round(((profile or {}).get("settings", {}).get("personal_rate") or 0.35) * 100, 1),
                 "min": 0, "max": 50, "step": 0.5, "show_if": {"kind": "private"},
                 "help": "Steuersatz auf jeden zusätzlich verdienten Euro. Bei 45.000 € Brutto im Jahr etwa 30 %, "
                         "bei 80.000 € etwa 40 %. Genau rechnet es „Welchen Steuersatz habe ich?“."},
                {"key": "married", "label": "Verheiratet (2.000 € Freibetrag)", "type": "toggle",
                 "default": bool(_fin(profile, "married", False))},
                {**CHURCH, "default": str(_fin(profile, "church", "none"))},
            ], icon="🏦", order=20)
def gaintax(con, profile, v):
    cur = cur_of(profile)
    gain = v["gain"] or 0
    if gain <= 0:
        raise CalcError("Bitte trag deinen Gewinn ein.")
    church = taxde.church_rate(v["church"])
    allowance = (2000 if v["married"] else 1000) - (v["used"] or 0)
    allowance = max(allowance, 0)
    k = v["kind"]
    if k == "private":
        if v["long"]:
            tax, why = 0.0, "Du hattest die Anlage länger als ein Jahr, deshalb ist der Gewinn steuerfrei."
        elif gain < taxde.PRIVATE_FREIGRENZE:
            tax, why = 0.0, ("Unter 1.000 € Gewinn im Jahr bleibt alles steuerfrei (Freigrenze). Achtung: Ab 1.000 € "
                             "wird der ganze Gewinn versteuert, nicht nur der Teil darüber.")
        else:
            tax = gain * (v["rate"] or 0) / 100
            why = (f"Du hast innerhalb eines Jahres verkauft. Deshalb zahlst du auf den ganzen Gewinn deinen "
                   f"Grenzsteuersatz ({fmt_num(v['rate'], 1)} %). Nach mehr als einem Jahr wäre er steuerfrei.")
    else:
        share = {"etf": 0.7, "bond": 1.0, "stock": 1.0}[k]
        taxable = max(gain * share - allowance, 0)
        tax = taxable * taxde.capital_rate(church)
        parts = []
        if share < 1:
            parts.append("30 % des Gewinns sind bei Aktien-ETFs steuerfrei (Teilfreistellung)")
        parts.append(f"{fmt_money(min(allowance, gain * share), cur)} deckt dein Freibetrag ab")
        why = (", ".join(parts) + f". Auf den Rest ({fmt_money(taxable, cur)}) zahlst du "
               f"{fmt_num(taxde.capital_rate(church) * 100, 2)} % Abgeltungsteuer inklusive Solidaritätszuschlag"
               + (" und Kirchensteuer." if church else "."))
    net = gain - tax
    return {
        "tiles": [{"label": "Steuer", "value": fmt_money(tax, cur), "tone": "bad" if tax else "good"},
                  {"label": "Gewinn nach Steuern", "value": fmt_money(net, cur), "tone": "accent"},
                  {"label": "Steueranteil", "value": f"{fmt_num(tax / gain * 100, 1)} %", "note": "vom Gewinn"}],
        "text": [f"Von {fmt_money(gain, cur)} Gewinn bleiben dir nach Steuern {fmt_money(net, cur)}.", why],
        "notes": ["Vereinfacht: Verluste aus anderen Verkäufen und die Vorabpauschale (eine kleine jährliche "
                  "Steuer-Vorauszahlung auf ETFs) sind nicht berücksichtigt. Keine Steuerberatung."],
    }
