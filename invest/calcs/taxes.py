"""Steuer-Rechner (Deutschland 2026, vereinfacht): eigener Steuersatz und Steuer auf Anlagegewinne."""
from __future__ import annotations

from .. import taxde
from ..units import fmt_money, fmt_num
from . import CalcError, calculator, cur_of, money_field

CHURCH = {"key": "church", "label": "Kirchensteuer", "type": "select", "default": "none",
          "options": [["none", "keine"], ["8", "8 % (Bayern, Baden-Württemberg)"], ["9", "9 % (andere Länder)"]]}


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
         "options": [["gross", "Mein Bruttogehalt im Jahr (Schätzung)"], ["zve", "Mein zu versteuerndes Einkommen"]]},
        money_field("gross", "Bruttogehalt im Jahr", 45000, "Steht auf der Lohnabrechnung (Dezember: Jahressumme).",
                    show_if={"mode": "gross"}),
        money_field("zve", "Zu versteuerndes Einkommen im Jahr", zve or 36000,
                    "Steht im letzten Steuerbescheid.", show_if={"mode": "zve"}),
        {"key": "married", "label": "Verheiratet / zusammen veranlagt", "type": "toggle",
         "default": bool(_fin(profile, "married", False))},
        {**CHURCH, "default": str(_fin(profile, "church", "none"))},
    ]


@calculator("taxrate", "Welchen Steuersatz habe ich?", "Grenz- und Durchschnittssteuersatz – und was das für Gold, "
            "Krypto, Aktien und ETFs bedeutet.", "cost", _rate_fields, icon="🧾", order=10)
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
        text.append(f"Geschätztes zu versteuerndes Einkommen: {fmt_money(zve, cur)} (Brutto minus Sozialabgaben und "
                    "Werbungskosten-Pauschale – grobe Schätzung).")
    text += [
        f"Du zahlst im Schnitt {fmt_num(r['average'] * 100, 1)} % Einkommensteuer"
        + (" inklusive Soli/Kirchensteuer" if church or r["tax"] > 20_350 else "") + f" ({fmt_money(r['tax'], cur)} im Jahr).",
        f"Jeder zusätzliche Euro wird mit {fmt_num(r['marginal'] * 100, 1)} % besteuert (Grenzsteuersatz).",
        f"Gold, Silber, Krypto: Verkaufst du innerhalb eines Jahres mit Gewinn, gilt dein Grenzsteuersatz "
        f"({fmt_num(r['marginal'] * 100, 0)} %) – sobald die Gewinne im Jahr 1.000 € erreichen, auf den ganzen Gewinn. "
        f"Nach einem Jahr Haltedauer: steuerfrei.",
        f"Aktien, ETFs, Fonds: immer Abgeltungsteuer {fmt_num(cap * 100, 2)} %, unabhängig vom Einkommen. "
        f"Die ersten {fmt_money(allowance, cur)} Kapitalerträge im Jahr sind frei (Freistellungsauftrag bei der Bank "
        f"stellen!). Bei Aktien-ETFs sind zusätzlich 30 % des Gewinns steuerfrei.",
    ]
    if r["marginal"] < 0.25:
        text.append("Dein Grenzsteuersatz liegt unter 25 %: Mit der Günstigerprüfung in der Steuererklärung bekommst "
                    "du einen Teil der Abgeltungsteuer zurück.")
    xs = list(range(0, 150_001, 5_000))
    curve = [taxde.rates(x, married, church) for x in xs]
    return {
        "tiles": [{"label": "Grenzsteuersatz", "value": f"{fmt_num(r['marginal'] * 100, 1)} %", "tone": "accent"},
                  {"label": "Durchschnittssteuersatz", "value": f"{fmt_num(r['average'] * 100, 1)} %"},
                  {"label": "Steuer im Jahr", "value": fmt_money(r["tax"], cur)}],
        "text": text,
        "charts": [{"type": "line", "title": "Steuersatz nach Einkommen", "unit": "pct",
                    "x": [f"{x // 1000} Tsd." for x in xs],
                    "series": [{"name": "Grenzsteuersatz", "role": "depot", "data": [round(c["marginal"] * 100, 1) for c in curve]},
                               {"name": "Durchschnitt", "role": "invest", "data": [round(c["average"] * 100, 1) for c in curve]}],
                    "note": f"Dein Einkommen: {fmt_money(zve, cur)}"}],
        "actions": [{"label": "Als meinen Steuersatz speichern", "type": "save_finance",
                     "data": {"zve": round(zve), "married": married, "church": v["church"]}}],
        "notes": ["Einkommensteuertarif 2026 (§ 32a EStG), Soli mit Freigrenze, Kirchensteuer vereinfacht. "
                  "Keine Steuerberatung."],
    }


@calculator("gaintax", "Wie viel Steuer zahle ich auf meinen Gewinn?", "Gewinn aus Verkauf: Was bleibt nach "
            "Steuern – je nach Anlage und Haltedauer?", "cost", lambda profile: [
                {"key": "kind", "label": "Was hast du verkauft?", "type": "select", "default": "etf",
                 "options": [["etf", "Aktien-ETF oder Aktienfonds"], ["bond", "Anleihen-ETF / sonstiger Fonds"],
                             ["stock", "Einzelaktie"], ["private", "Gold, Silber, Krypto (physisch / direkt)"]]},
                money_field("gain", "Gewinn", 5000),
                {"key": "long", "label": "Länger als 1 Jahr gehalten", "type": "toggle", "default": True,
                 "show_if": {"kind": "private"}},
                money_field("used", "Schon genutzter Sparerpauschbetrag dieses Jahr", 0, show_if={"kind": "etf"}),
                {"key": "rate", "label": "Dein Grenzsteuersatz in %", "type": "percent",
                 "default": round(((profile or {}).get("settings", {}).get("personal_rate") or 0.35) * 100, 1),
                 "min": 0, "max": 50, "step": 0.5, "show_if": {"kind": "private"}},
                {"key": "married", "label": "Verheiratet (2.000 € Freibetrag)", "type": "toggle",
                 "default": bool(_fin(profile, "married", False))},
                {**CHURCH, "default": str(_fin(profile, "church", "none"))},
            ], icon="🏦", order=20)
def gaintax(con, profile, v):
    cur = cur_of(profile)
    gain = v["gain"] or 0
    if gain <= 0:
        raise CalcError("Bitte einen Gewinn eingeben.")
    church = taxde.church_rate(v["church"])
    allowance = (2000 if v["married"] else 1000) - (v["used"] or 0)
    allowance = max(allowance, 0)
    k = v["kind"]
    if k == "private":
        if v["long"]:
            tax, why = 0.0, "Nach mehr als einem Jahr Haltedauer ist der Gewinn steuerfrei (§ 23 EStG)."
        elif gain < taxde.PRIVATE_FREIGRENZE:
            tax, why = 0.0, ("Unter 1.000 € Gewinn im Jahr (Freigrenze) bleibt alles steuerfrei. Achtung: Ab 1.000 € "
                             "wird der ganze Gewinn versteuert, nicht nur der Teil darüber.")
        else:
            tax = gain * (v["rate"] or 0) / 100
            why = (f"Innerhalb eines Jahres verkauft: der ganze Gewinn wird mit deinem Grenzsteuersatz "
                   f"({fmt_num(v['rate'], 1)} %) versteuert. Ein paar Wochen länger halten kann viel sparen.")
    else:
        share = {"etf": 0.7, "bond": 1.0, "stock": 1.0}[k]
        taxable = max(gain * share - allowance, 0)
        tax = taxable * taxde.capital_rate(church)
        parts = []
        if share < 1:
            parts.append("30 % des Gewinns sind steuerfrei (Teilfreistellung für Aktienfonds)")
        parts.append(f"{fmt_money(min(allowance, gain * share), cur)} fallen unter den Sparerpauschbetrag")
        why = (", ".join(parts) + f"; auf den Rest ({fmt_money(taxable, cur)}) kommen "
               f"{fmt_num(taxde.capital_rate(church) * 100, 2)} % Abgeltungsteuer inkl. Soli"
               + (" und Kirchensteuer." if church else "."))
    net = gain - tax
    return {
        "tiles": [{"label": "Steuer", "value": fmt_money(tax, cur), "tone": "bad" if tax else "good"},
                  {"label": "Gewinn nach Steuern", "value": fmt_money(net, cur), "tone": "accent"},
                  {"label": "Steuerquote", "value": f"{fmt_num(tax / gain * 100, 1)} %"}],
        "text": [f"Von {fmt_money(gain, cur)} Gewinn bleiben {fmt_money(net, cur)}.", why],
        "notes": ["Vereinfacht: keine Verlustverrechnung, keine Vorabpauschale. Keine Steuerberatung."],
    }
