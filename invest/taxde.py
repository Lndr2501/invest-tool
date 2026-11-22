"""Deutsche Einkommensteuer 2026 (§ 32a EStG) mit Soli und Kirchensteuer – für „Welcher Steuersatz gilt für mich?“.

Quelle Tarif: § 32a Abs. 1 EStG ab Veranlagungszeitraum 2026 (gesetze-im-internet.de, geprüft 22.11.2026).
Soli: Freigrenze 20.350 € Einkommensteuer (zusammen 40.700 €), Milderungszone 11,9 % des übersteigenden Betrags.
"""
from __future__ import annotations

GRUNDFREIBETRAG = 12_348
SOLI_FREIGRENZE = 20_350
SPARERPAUSCHBETRAG = 1_000  # verheiratet 2.000
ABGELTUNG = 0.25
PRIVATE_FREIGRENZE = 1_000  # § 23 EStG: Gewinne aus privaten Veräußerungen (Gold, Krypto) pro Jahr


def income_tax_single(zve: float) -> float:
    x = int(max(zve, 0))
    if x <= GRUNDFREIBETRAG:
        return 0.0
    if x <= 17_799:
        y = (x - GRUNDFREIBETRAG) / 10_000
        t = (914.51 * y + 1_400) * y
    elif x <= 69_878:
        z = (x - 17_799) / 10_000
        t = (173.10 * z + 2_397) * z + 1_034.87
    elif x <= 277_825:
        t = 0.42 * x - 11_135.63
    else:
        t = 0.45 * x - 19_470.38
    return float(int(t))


def income_tax(zve: float, married: bool = False) -> float:
    """Splitting: doppelte Steuer auf das halbe gemeinsame Einkommen."""
    return 2 * income_tax_single(zve / 2) if married else income_tax_single(zve)


def soli(tax: float, married: bool = False) -> float:
    free = SOLI_FREIGRENZE * (2 if married else 1)
    if tax <= free:
        return 0.0
    return min(tax * 0.055, (tax - free) * 0.119)


def total_tax(zve: float, married: bool = False, church: float = 0.0) -> dict:
    est = income_tax(zve, married)
    so = soli(est, married)
    ki = est * church
    return {"est": est, "soli": so, "church": ki, "total": est + so + ki}


def rates(zve: float, married: bool = False, church: float = 0.0) -> dict:
    """Durchschnitts- und Grenzsteuersatz (auf 1.000 € mehr Einkommen) inklusive Soli und Kirchensteuer."""
    now = total_tax(zve, married, church)["total"]
    more = total_tax(zve + 1_000, married, church)["total"]
    return {"average": now / zve if zve > 0 else 0.0, "marginal": (more - now) / 1_000, "tax": now}


def capital_rate(church: float = 0.0) -> float:
    """Abgeltungsteuer inkl. Soli (und Kirchensteuer, die die Abgeltungsteuer mindert)."""
    if church:
        base = ABGELTUNG / (1 + ABGELTUNG * church)
        return base * (1 + 0.055 + church)
    return ABGELTUNG * 1.055


def church_rate(state_or_rate) -> float:
    """'none' | 8 | 9 | 'BY' | 'BW' … → Satz (Bayern und Baden-Württemberg 8 %, sonst 9 %)."""
    if state_or_rate in (None, "", "none", 0, "0"):
        return 0.0
    if str(state_or_rate) in ("8", "BY", "BW"):
        return 0.08
    return 0.09
