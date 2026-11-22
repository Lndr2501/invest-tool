"""Start-Assistent: aus ein paar einfachen Antworten einen ersten Plan machen (Reihenfolge, Sparrate, Mischung).

Regeln bewusst einfach und vorsichtig (Einsteiger):
* Erst teure Schulden, dann Notgroschen (3 Monatsausgaben), dann anlegen.
* Zeitraum entscheidet über die Art: < 3 Jahre Tagesgeld, 3–10 Jahre Mischung mit Anleihen, > 10 Jahre Aktien-ETF.
* Risiko verschiebt nur innerhalb dieser Leitplanken; Gold/Krypto höchstens als kleine Beimischung.
Der Assistent (KI) und die Website nutzen dieselbe Funktion ``suggest``.
"""
from __future__ import annotations

from . import service
from .config import ASSETS
from .units import fmt_money, fmt_num

CASH_RATE = 0.02  # Tagesgeld-Annahme pro Jahr

# (bis Jahre, Risiko 1…5) → Mischung in Prozent. Schlüssel: world, bonds, gold, crypto
MIXES = {
    "mid": {1: {"bonds": 70, "world": 30}, 2: {"bonds": 60, "world": 40}, 3: {"bonds": 50, "world": 50},
            4: {"bonds": 40, "world": 60}, 5: {"bonds": 30, "world": 65, "gold": 5}},
    "long": {1: {"world": 50, "bonds": 50}, 2: {"world": 70, "bonds": 30}, 3: {"world": 90, "gold": 10},
             4: {"world": 85, "gold": 10, "crypto": 5}, 5: {"world": 80, "gold": 10, "crypto": 10}},
}
BLOCKS = {"world": "MSCIW", "bonds": "EUGOV", "gold": "GOLD", "crypto": "BTC"}
BLOCK_TEXT = {
    "world": "Welt-Aktien-ETF: über 1.000 große Firmen aus vielen Ländern in einem Produkt. Der Motor für langfristiges "
             "Wachstum – schwankt aber, auch mal um 30–50 %.",
    "bonds": "Anleihen-ETF (Staatsanleihen): wenig Rendite, aber deutlich ruhiger. Bremst Abstürze ab.",
    "gold": "Gold: bringt keine Zinsen, läuft aber oft anders als Aktien. Kleine Beimischung zur Absicherung.",
    "crypto": "Bitcoin: sehr starke Schwankungen (Abstürze um 70–80 % gab es mehrmals). Nur als kleine Beimischung, "
              "mit Geld, dessen Verlust du verkraften würdest.",
    "cash": "Tagesgeld: sicher und jederzeit verfügbar, Zinsen etwa 1,5–3 %. Für Geld, das du bald brauchst.",
}
REACTION_RISK = {"sell": 1, "worry": 2, "wait": 3, "buy": 4, "buy_more": 5}


def horizon(years: float) -> str:
    return "short" if years < 3 else ("mid" if years <= 10 else "long")


def mix_for(years: float, risk: int) -> dict[str, int]:
    h = horizon(years)
    if h == "short":
        return {"cash": 100}
    return dict(MIXES[h][min(max(int(risk or 3), 1), 5)])


def suggest(con, profile: dict, a: dict) -> dict:
    cur = profile["settings"]["currency"]
    years = min(max(float(a.get("years") or 10), 1), 40)
    risk = int(a.get("risk") or REACTION_RISK.get(a.get("reaction"), 3))
    income, expenses = float(a.get("income") or 0), float(a.get("expenses") or 0)
    cash, debt = float(a.get("cash") or 0), float(a.get("debt") or 0)
    target = float(a.get("amount") or 0)
    free = income - expenses if income else None
    monthly = float(a.get("monthly") or 0)
    monthly_source = "own"
    if not monthly and free and free > 0:
        monthly = round(min(free * 0.7, max(free, 0) if free < income * 0.2 else income * 0.2) / 5) * 5
        monthly_source = "income"
    emergency = 3 * expenses if expenses else None
    gap = max(emergency - cash, 0) if emergency else 0
    purpose = a.get("purpose") or "wealth"
    if purpose == "emergency":  # Ziel ist der Notgroschen selbst
        years = min(years, 2)
    mix = mix_for(years, risk)
    # nur Bausteine mit Kursdaten verwenden (sonst Welt-ETF)
    weights = {}
    for k, v in mix.items():
        if k == "cash":
            continue
        asset = BLOCKS[k] if BLOCKS[k] in ASSETS and service._known(con, BLOCKS[k]) else "MSCIW"
        weights[asset] = weights.get(asset, 0) + v

    steps = []
    if debt:
        steps.append({"title": "Teure Schulden tilgen", "text": f"Zuerst die {fmt_money(debt, cur)} Schulden abbezahlen. "
                      "Dispo- und Ratenkredite kosten oft 8–15 % Zinsen – das schlägt jede Anlage."})
    if gap > 0:
        months = gap / monthly if monthly else None
        steps.append({"title": "Notgroschen aufbauen", "text": f"{fmt_money(emergency, cur)} (3 Monatsausgaben) auf ein "
                      f"Tagesgeldkonto. Dir fehlen {fmt_money(gap, cur)}"
                      + (f" – mit {fmt_money(monthly, cur)} im Monat etwa {fmt_num(months, 0)} Monate." if months else ".")})
    if purpose != "emergency":
        h = horizon(years)
        how = {"short": "auf Tagesgeld oder Festgeld – bei weniger als 3 Jahren ist Schwankendes zu riskant",
               "mid": "in eine Mischung aus Anleihen- und Aktien-ETF", "long": "breit gestreut in einen Welt-Aktien-ETF"}[h]
        steps.append({"title": "Regelmäßig anlegen", "text": f"{fmt_money(monthly, cur) if monthly else 'Ein fester Betrag'} "
                      f"im Monat {how}. Am einfachsten als Sparplan bei einer günstigen Bank oder einem Neobroker "
                      "(Sparplan-Ausführung oft kostenlos)."})
    steps.append({"title": "Dranbleiben", "text": "Einmal im Jahr prüfen, nicht bei jeder Schlagzeile verkaufen. "
                  "Gehaltserhöhung? Sparrate mit erhöhen."})

    forecast = None
    goal_text = None
    if monthly > 0 or cash > (emergency or 0):
        if "cash" in mix:
            n = int(years * 12)
            i = (1 + CASH_RATE) ** (1 / 12) - 1
            fv = monthly * (((1 + i) ** n - 1) / i) * (1 + i)
            forecast = {"kind": "cash", "invested": monthly * n, "p50": fv, "p5": monthly * n, "p95": fv,
                        "text": f"Auf Tagesgeld mit {fmt_num(CASH_RATE * 100, 1)} % Zinsen hast du nach "
                                f"{fmt_num(years, 0)} Jahren etwa {fmt_money(fv, cur)} ({fmt_money(monthly * n, cur)} eingezahlt)."}
        else:
            try:
                r = service.calc_future(con, profile, {"initial": 0, "monthly": monthly, "years": years,
                                                       "weights": weights, "drift": "careful", "goal": target or None})
                forecast = {"kind": "sim", "invested": r["invested"], "p5": r["end"][5], "p50": r["end"][50],
                            "p95": r["end"][95], "p_loss": r["p_loss"], "p_goal": r.get("p_goal"),
                            "text": f"Mit {fmt_money(monthly, cur)} im Monat hast du nach {fmt_num(years, 0)} Jahren im "
                                    f"mittleren Fall etwa {fmt_money(r['end'][50], cur)} (eingezahlt "
                                    f"{fmt_money(r['invested'], cur)}). Schlechter Fall (1 von 20): "
                                    f"{fmt_money(r['end'][5], cur)}, guter Fall: {fmt_money(r['end'][95], cur)}."}
            except ValueError:
                forecast = None
    if target:
        plan = service.goal_plan({"amount": target, "years": years})
        if plan:
            ok = monthly >= plan["monthly"] * 0.95
            goal_text = (f"Für {fmt_money(target, cur)} in {fmt_num(years, 0)} Jahren brauchst du etwa "
                         f"{fmt_money(plan['monthly'], cur)} im Monat ({plan['label']}, vorsichtig gerechnet). "
                         + ("Deine Sparrate reicht." if ok else
                            f"Mit {fmt_money(monthly, cur)} fehlt noch etwas – mehr sparen, länger warten oder "
                            "das Ziel anpassen."))
    mix_rows = [{"key": k, "pct": v, "asset": BLOCKS.get(k), "text": BLOCK_TEXT[k],
                 "name": {"world": "Welt-Aktien-ETF", "bonds": "Anleihen-ETF", "gold": "Gold", "crypto": "Bitcoin",
                          "cash": "Tagesgeld"}[k]} for k, v in mix.items()]
    return {
        "years": years, "risk": risk, "horizon": horizon(years), "monthly": monthly, "monthly_source": monthly_source,
        "free": free, "emergency": emergency, "gap": gap, "mix": mix_rows, "weights": weights, "steps": steps,
        "forecast": forecast, "goal_text": goal_text, "currency": cur,
        "save": {"income": income or None, "expenses": expenses or None, "cash": cash, "debt": debt or None,
                 "saving": monthly or None, "risk": risk, "experience": a.get("experience") or None},
    }
