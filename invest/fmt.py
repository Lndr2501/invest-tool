"""Deutsche Zahlenformate."""
from __future__ import annotations


def num(x: float | None, digits: int = 0) -> str:
    if x is None:
        return "–"
    s = f"{x:,.{digits}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def eur(x: float | None, digits: int | None = None) -> str:
    if x is None:
        return "–"
    if digits is None:
        digits = 0 if abs(x) >= 1000 else 2
    return f"{num(x, digits)} €"


def pct(x: float | None, digits: int = 1, sign: bool = True) -> str:
    if x is None:
        return "–"
    v = x * 100
    s = num(abs(v), digits)
    if sign:
        s = ("+" if v > 0 else "−" if v < 0 else "±") + s
    elif v < 0:
        s = "−" + s
    return f"{s} %"


def delta(x: float | None, digits: int = 1, suffix: str = "") -> str | None:
    """Für st.metric: Vorzeichen als ASCII-Minus, sonst erkennt Streamlit die Richtung nicht."""
    if x is None:
        return None
    return pct(x, digits).replace("−", "-").replace("±", "") + suffix
