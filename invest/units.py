"""Einheiten, Feinheiten, Münzen und Währungen.

Alle Edelmetall-Kurse liegen intern als EUR je Feinunze (troy oz) vor. Hier wird umgerechnet:
Menge × Gewicht × Feinheit → Feinunzen, und Feinunzen-Preis → Preis je Gramm/Kilo/Unze einer Feinheit.
"""
from __future__ import annotations

OZ = 31.1034768  # Gramm je Feinunze (troy ounce)

WEIGHT_UNITS = {"g": 1.0, "kg": 1000.0, "oz": OZ}
UNIT_LABEL = {"g": "Gramm", "kg": "Kilo", "oz": "Unze"}

# Feinheit in Promille → Anzeige
FINENESS = {
    "GOLD": [
        (999.9, "999,9 · 24 Karat (Barren, Philharmoniker, Maple Leaf)"),
        (986.0, "986 · 23,6 Karat (Dukaten)"),
        (916.7, "916,7 · 22 Karat (Krügerrand, Sovereign, Eagle)"),
        (900.0, "900 · 21,6 Karat (Vreneli, alte Münzen)"),
        (750.0, "750 · 18 Karat (Schmuck)"),
        (585.0, "585 · 14 Karat (Schmuck)"),
        (375.0, "375 · 9 Karat (Schmuck)"),
        (333.0, "333 · 8 Karat (Schmuck)"),
    ],
    "SILVER": [
        (999.0, "999 · Feinsilber (Barren, Anlagemünzen)"),
        (958.0, "958 · Britannia-Silber"),
        (925.0, "925 · Sterlingsilber (Besteck, Schmuck, 10-€-Münzen bis 2010)"),
        (900.0, "900 · Münzsilber"),
        (835.0, "835 · altes Silberbesteck"),
        (800.0, "800 · altes Silberbesteck"),
        (625.0, "625 · 10-DM- und 10-€-Münzen ab 2011"),
    ],
    "PLATINUM": [
        (999.5, "999,5 · Barren und Anlagemünzen"),
        (950.0, "950 · Platinschmuck"),
        (900.0, "900 · Platinschmuck"),
    ],
    "PALLADIUM": [
        (999.5, "999,5 · Barren und Anlagemünzen"),
        (950.0, "950 · Palladiumschmuck"),
        (500.0, "500 · Schmuck"),
    ],
}
DEFAULT_FINENESS = {"GOLD": 999.9, "SILVER": 999.0, "PLATINUM": 999.5, "PALLADIUM": 999.5}

# Münzen: Rohgewicht in g und Feinheit → Feingewicht ergibt sich
COINS = {
    "GOLD": [
        {"id": "philharmoniker", "name": "Wiener Philharmoniker", "sizes": True, "weight": OZ, "fineness": 999.9},
        {"id": "maple", "name": "Maple Leaf", "sizes": True, "weight": OZ, "fineness": 999.9},
        {"id": "kruegerrand", "name": "Krügerrand", "sizes": True, "weight": 33.93, "fineness": 916.7},
        {"id": "eagle", "name": "American Eagle", "sizes": True, "weight": 33.93, "fineness": 916.7},
        {"id": "britannia", "name": "Britannia", "sizes": True, "weight": OZ, "fineness": 999.9},
        {"id": "kangaroo", "name": "Känguru / Nugget", "sizes": True, "weight": OZ, "fineness": 999.9},
        {"id": "vreneli", "name": "Vreneli 20 Franken", "sizes": False, "weight": 6.4516, "fineness": 900.0},
        {"id": "sovereign", "name": "Sovereign", "sizes": False, "weight": 7.9881, "fineness": 916.7},
        {"id": "napoleon", "name": "20 Francs Napoleon", "sizes": False, "weight": 6.4516, "fineness": 900.0},
        {"id": "dukat1", "name": "1 Dukat Österreich", "sizes": False, "weight": 3.4909, "fineness": 986.0},
        {"id": "dukat4", "name": "4 Dukaten Österreich", "sizes": False, "weight": 13.9636, "fineness": 986.0},
    ],
    "SILVER": [
        {"id": "s_philharmoniker", "name": "Wiener Philharmoniker", "sizes": False, "weight": OZ, "fineness": 999.0},
        {"id": "s_maple", "name": "Maple Leaf", "sizes": False, "weight": OZ, "fineness": 999.9},
        {"id": "s_kruegerrand", "name": "Krügerrand", "sizes": False, "weight": OZ, "fineness": 999.0},
        {"id": "s_eagle", "name": "American Eagle", "sizes": False, "weight": OZ, "fineness": 999.0},
        {"id": "s_britannia", "name": "Britannia", "sizes": False, "weight": OZ, "fineness": 999.0},
        {"id": "dm10_625", "name": "10 DM Gedenkmünze (1987–1997)", "sizes": False, "weight": 15.5, "fineness": 625.0},
        {"id": "dm10_925", "name": "10 DM Gedenkmünze (1998–2001)", "sizes": False, "weight": 15.5, "fineness": 925.0},
        {"id": "eur10_925", "name": "10 € Gedenkmünze (2002–2010)", "sizes": False, "weight": 18.0, "fineness": 925.0},
        {"id": "eur10_625", "name": "10 € Gedenkmünze (2011–2015)", "sizes": False, "weight": 16.0, "fineness": 625.0},
    ],
    "PLATINUM": [
        {"id": "p_maple", "name": "Maple Leaf Platin", "sizes": True, "weight": OZ, "fineness": 999.5},
        {"id": "p_philharmoniker", "name": "Wiener Philharmoniker Platin", "sizes": True, "weight": OZ, "fineness": 999.5},
    ],
    "PALLADIUM": [
        {"id": "pd_maple", "name": "Maple Leaf Palladium", "sizes": False, "weight": OZ, "fineness": 999.5},
    ],
}
COIN_SIZES = [(1.0, "1 oz"), (0.5, "1/2 oz"), (0.25, "1/4 oz"), (0.1, "1/10 oz"), (0.05, "1/20 oz")]
COIN_BY_ID = {c["id"]: c for lst in COINS.values() for c in lst}

# Währungen: Code → (Zeichen, Name). EUR ist die Basis, alle anderen kommen von der EZB.
CURRENCIES = {
    "EUR": ("€", "Euro"),
    "CHF": ("CHF", "Schweizer Franken"),
    "USD": ("$", "US-Dollar"),
    "GBP": ("£", "Britisches Pfund"),
    "PLN": ("zł", "Polnischer Złoty"),
    "CZK": ("Kč", "Tschechische Krone"),
    "SEK": ("kr", "Schwedische Krone"),
    "DKK": ("kr", "Dänische Krone"),
    "NOK": ("kr", "Norwegische Krone"),
    "HUF": ("Ft", "Ungarischer Forint"),
    "TRY": ("₺", "Türkische Lira"),
}
# Zusätzlich nur zum Umrechnen von Kursen (Aktien/Fonds an anderen Börsen)
FX_EXTRA = ["JPY", "CAD", "AUD", "HKD", "ZAR", "ILS"]
FX_CODES = [c for c in CURRENCIES if c != "EUR"] + FX_EXTRA

# Inflation (Eurostat HVPI)
INFLATION_GEOS = {"DE": "Deutschland", "AT": "Österreich", "CH": "Schweiz", "EA": "Euroraum"}


def fine_grams(count: float, weight: float, unit: str, fineness: float) -> float:
    return float(count) * float(weight) * WEIGHT_UNITS[unit] * float(fineness) / 1000.0


def coin_fine_grams(coin_id: str, size: float = 1.0) -> float:
    c = COIN_BY_ID[coin_id]
    return c["weight"] * (size if c["sizes"] else 1.0) * c["fineness"] / 1000.0


def price_per(price_per_oz: float, unit: str = "g", fineness: float = 999.9) -> float:
    """Feinunzen-Preis → Preis für 1 g / 1 kg / 1 oz Material der Feinheit (reiner Materialwert)."""
    return price_per_oz / OZ * WEIGHT_UNITS[unit] * fineness / 1000.0


def fmt_num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "–"
    s = f"{x:,.{digits}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_money(x: float | None, cur: str = "EUR", digits: int | None = None) -> str:
    if x is None:
        return "–"
    if digits is None:
        digits = 0 if abs(x) >= 1000 else 2 if abs(x) >= 1 or x == 0 else 4
    sym = CURRENCIES.get(cur, (cur,))[0]
    return f"{fmt_num(x, digits)} {sym}"


def fineness_label(f: float) -> str:
    return fmt_num(f, 1 if f % 1 else 0)
