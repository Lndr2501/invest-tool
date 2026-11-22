"""Markt-Registry: welche Anlagen es gibt, woher ihre Kurse kommen und wie sie besteuert werden.

Erweiterbar an zwei Stellen:
* ``CATALOG`` unten – fest eingebaute Märkte (ein Eintrag = ein Dict, siehe ``_m``).
* Tabelle ``markets`` – Märkte, die Nutzer über die Suche hinzufügen (Yahoo-Kürzel).

``config.ASSETS`` wird hier *in place* befüllt, damit aller bestehende Code (``from .config import ASSETS``)
automatisch alle Märkte sieht. ``sync(con)`` lädt nach, wenn jemand einen Markt hinzugefügt hat.
"""
from __future__ import annotations

import copy
import json
import re
import time

from .config import ASSETS

# Art → Voreinstellungen. tax: private = § 23 EStG (1 Jahr Haltefrist), fund_equity = Aktienfonds
# (30 % Teilfreistellung), fund_other = Fonds ohne Teilfreistellung (z. B. Anleihen), stock = Einzelaktie.
KINDS: dict[str, dict] = {
    "crypto": {"label": "Krypto", "tax": "private", "always_open": True, "unit": "Coin", "units": "Coins",
               "costs": {"buy_fee": 0.005, "spread": 0.01, "custody_pa": 0.0}, "cap_pa": 0.15, "risk": 5},
    "metal": {"label": "Edelmetalle", "tax": "private", "always_open": False, "unit": "Feinunze", "units": "Feinunzen",
              "costs": {"buy_fee": 0.0025, "spread": 0.02, "custody_pa": 0.003}, "cap_pa": 0.04, "risk": 3},
    "stock": {"label": "Aktien", "tax": "stock", "always_open": False, "unit": "Aktie", "units": "Aktien",
              "costs": {"buy_fee": 0.001, "spread": 0.002, "custody_pa": 0.0}, "cap_pa": 0.08, "risk": 4},
    "etf": {"label": "ETFs", "tax": "fund_equity", "always_open": False, "unit": "Anteil", "units": "Anteile",
            "costs": {"buy_fee": 0.001, "spread": 0.002, "custody_pa": 0.0}, "cap_pa": 0.07, "risk": 3},
    "bond": {"label": "Anleihen", "tax": "fund_other", "always_open": False, "unit": "Anteil", "units": "Anteile",
             "costs": {"buy_fee": 0.001, "spread": 0.002, "custody_pa": 0.0}, "cap_pa": 0.03, "risk": 2},
    "fund": {"label": "Fonds", "tax": "fund_equity", "always_open": False, "unit": "Anteil", "units": "Anteile",
             "costs": {"buy_fee": 0.0, "spread": 0.0, "custody_pa": 0.0}, "cap_pa": 0.06, "risk": 3},
}
KIND_ORDER = ["etf", "crypto", "metal", "stock", "bond", "fund"]
# Metalle mit Gewicht/Feinheit (Schlüssel in den Profil-Einstellungen)
METALS = {"GOLD": "gold", "SILVER": "silver", "PLATINUM": "platinum", "PALLADIUM": "palladium"}
CORE = ["MSCIW", "GOLD", "SILVER", "BTC"]  # Standard-Beobachtungsliste

# Yahoo-quoteType → Art
YAHOO_KIND = {"EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "fund", "CRYPTOCURRENCY": "crypto"}


def _m(mid, name, kind, daily, desc, *, short=None, intraday=None, spot=None, isin=None, tax=None, costs=None,
       always_open=None, cur=None, adjusted=None):
    k = KINDS[kind]
    return mid, {
        "id": mid, "name": name, "short": short or name, "kind": kind, "desc": desc, "isin": isin,
        "unit": k["unit"], "units": k["units"],
        "daily": ("yahoo", daily),
        "intraday": intraday or [("yahoo", daily, cur or "EUR")],
        "spot": spot, "tax": tax or k["tax"],
        "always_open": k["always_open"] if always_open is None else always_open,
        "costs": costs, "builtin": True,
        # Dividenden einrechnen (bereinigter Schlusskurs) – nicht bei Metallen/Krypto/thesaurierenden ETFs nötig
        "adjusted": adjusted if adjusted is not None else kind in ("stock", "bond", "fund"),
    }


def _crypto(mid, name, desc, short=None):
    return _m(mid, name, "crypto", f"{mid}-EUR", desc, short=short or mid,
              intraday=[("binance", f"{mid}EUR", "EUR"), ("yahoo", f"{mid}-EUR", "EUR")])


CATALOG: dict[str, dict] = dict([
    # --- Kern (v1) ---
    _m("BTC", "Bitcoin", "crypto", "BTC-EUR",
       "Die älteste und größte Kryptowährung. Sehr starke Schwankungen: Abstürze um 70–80 % gab es mehrfach.",
       intraday=[("binance", "BTCEUR", "EUR"), ("kraken", "XBTEUR", "EUR")]),
    _m("GOLD", "Gold", "metal", "GC=F",
       "Wertspeicher seit Jahrtausenden. Bringt keine Zinsen, schwankt aber deutlich weniger als Aktien.",
       intraday=[("binance", "PAXGUSDT", "USD"), ("kraken", "PAXGEUR", "EUR")], spot="XAU", always_open=True,
       costs={"buy_fee": 0.0025, "spread": 0.005, "custody_pa": 0.003}),
    _m("SILVER", "Silber", "metal", "SI=F",
       "Edelmetall und Industrierohstoff. Schwankt deutlich stärker als Gold.",
       intraday=[("yahoo", "SI=F", "USD")], spot="XAG",
       costs={"buy_fee": 0.0025, "spread": 0.01, "custody_pa": 0.002}),
    _m("MSCIW", "MSCI World (ETF)", "etf", "EUNL.DE",
       "Rund 1.400 große Firmen aus 23 Industrieländern in einem Paket. Der Klassiker zum langfristigen Sparen.",
       short="MSCI World", isin="IE00B4L5Y983", costs={"buy_fee": 0.001, "spread": 0.002, "custody_pa": 0.002}),
    # --- Krypto ---
    _crypto("ETH", "Ethereum", "Zweitgrößte Kryptowährung, Plattform für Anwendungen. Schwankt noch stärker als Bitcoin."),
    _crypto("SOL", "Solana", "Junge Krypto-Plattform (seit 2020). Sehr spekulativ."),
    _crypto("XRP", "XRP", "Kryptowährung für Zahlungen zwischen Banken. Sehr spekulativ."),
    _crypto("ADA", "Cardano", "Krypto-Plattform aus der Forschung. Sehr spekulativ."),
    _crypto("DOGE", "Dogecoin", "Als Spaß gestartete Kryptowährung. Der Kurs hängt stark an Stimmung und Prominenten."),
    _crypto("LTC", "Litecoin", "Eine der ältesten Kryptowährungen, technisch ähnlich wie Bitcoin."),
    _crypto("LINK", "Chainlink", "Dienst, der Krypto-Anwendungen mit Daten versorgt. Sehr spekulativ."),
    _crypto("BNB", "BNB", "Kryptowährung der Börse Binance. Hängt am Erfolg eines einzelnen Unternehmens."),
    # --- Metalle ---
    _m("PLATINUM", "Platin", "metal", "PL=F",
       "Seltenes Edelmetall, vor allem für Katalysatoren und Schmuck gefragt. Schwankt stärker als Gold.",
       intraday=[("yahoo", "PL=F", "USD")], spot="XPT"),
    _m("PALLADIUM", "Palladium", "metal", "PA=F",
       "Edelmetall für Autokatalysatoren. Sehr starke Preissprünge in beide Richtungen.",
       intraday=[("yahoo", "PA=F", "USD")], spot="XPD"),
    # --- ETFs ---
    _m("FTSEAW", "FTSE All-World (ETF)", "etf", "VWCE.DE",
       "Rund 4.000 Firmen aus Industrie- und Schwellenländern – sehr breit gestreut.", short="FTSE All-World",
       isin="IE00BK5BQT80"),
    _m("ACWI", "MSCI ACWI IMI (ETF)", "etf", "SPYI.DE",
       "Industrie- und Schwellenländer inklusive kleiner Firmen – fast der ganze Aktienmarkt.", short="MSCI ACWI IMI",
       isin="IE00B3YLTY66"),
    _m("SP500", "S&P 500 (ETF)", "etf", "SXR8.DE", "Die 500 größten Firmen der USA.", short="S&P 500",
       isin="IE00B5BMR087"),
    _m("NDX", "Nasdaq 100 (ETF)", "etf", "SXRV.DE",
       "Die 100 größten, technologielastigen Firmen der US-Börse Nasdaq. Schwankt stärker als der breite Markt.",
       short="Nasdaq 100", isin="IE00B53SZB19"),
    _m("EM", "Schwellenländer (ETF)", "etf", "IS3N.DE", "Firmen aus Schwellenländern wie China, Indien, Taiwan und Brasilien.",
       short="Schwellenländer", isin="IE00BKM4GZ66"),
    _m("DAX", "DAX (ETF)", "etf", "EXS1.DE", "Die 40 größten deutschen Firmen.", short="DAX", isin="DE0005933931"),
    _m("STOXX600", "Europa – STOXX 600 (ETF)", "etf", "EXSA.DE", "600 Firmen aus ganz Europa.", short="STOXX 600",
       isin="DE0002635307", adjusted=True),
    _m("EUGOV", "Euro-Staatsanleihen (ETF)", "bond", "EUNH.DE",
       "Staatsanleihen aus dem Euroraum. Schwankt wenig und bringt Zinsen – das Gegengewicht zu Aktien.",
       short="Euro-Staatsanleihen", isin="IE00B4WXJJ64"),
    # --- Fonds ---
    _m("FUNDSMITH", "Fundsmith Equity (Fonds)", "fund", "0P0000RU81.L",
       "Aktiv verwalteter Aktienfonds mit rund 30 Qualitätsfirmen. Höhere laufende Kosten als ein ETF.",
       short="Fundsmith Equity", isin="LU0690375182", cur="GBP"),
    # --- Aktien ---
    _m("AAPL", "Apple", "stock", "AAPL", "iPhone-Hersteller und eines der wertvollsten Unternehmen der Welt.", cur="USD"),
    _m("MSFT", "Microsoft", "stock", "MSFT", "Software (Windows, Office) und Cloud.", cur="USD"),
    _m("NVDA", "Nvidia", "stock", "NVDA", "Grafik- und KI-Chips. Stark gestiegen und entsprechend schwankungsanfällig.",
       cur="USD"),
    _m("AMZN", "Amazon", "stock", "AMZN", "Onlinehandel und Cloud-Dienste (AWS).", cur="USD"),
    _m("GOOGL", "Alphabet (Google)", "stock", "GOOGL", "Google-Suche, YouTube und Werbung.", short="Alphabet", cur="USD"),
    _m("TSLA", "Tesla", "stock", "TSLA", "Elektroautos. Eine der am stärksten schwankenden großen Aktien.", cur="USD"),
    _m("SAP", "SAP", "stock", "SAP.DE", "Größter deutscher Softwarekonzern."),
    _m("SIE", "Siemens", "stock", "SIE.DE", "Industrie, Automatisierung, Bahn- und Gebäudetechnik."),
    _m("ALV", "Allianz", "stock", "ALV.DE", "Versicherung und Vermögensverwaltung, zahlt hohe Dividenden."),
    _m("NESN", "Nestlé", "stock", "NESN.SW", "Lebensmittel und Getränke. Gilt als eher ruhige Aktie.", cur="CHF"),
])

_state = {"sig": None}


def kind_of(asset: str) -> str:
    return (ASSETS.get(asset) or {}).get("kind", "etf")


def is_metal(asset: str) -> bool:
    return asset in METALS


def costs_for(asset: str) -> dict:
    a = ASSETS.get(asset) or {}
    return dict(a.get("costs") or KINDS[a.get("kind", "etf")]["costs"])


def cap_pa(asset: str) -> float:
    return KINDS[kind_of(asset)]["cap_pa"]


def public(asset: str) -> dict:
    """Für Browser/KI: ohne Quellen-Interna."""
    a = ASSETS[asset]
    return {"id": asset, "name": a["name"], "short": a.get("short", a["name"]), "kind": a["kind"],
            "kind_label": KINDS[a["kind"]]["label"], "desc": a.get("desc", ""), "isin": a.get("isin"),
            "unit": a["unit"], "units": a.get("units", a["unit"]), "metal": is_metal(asset),
            "symbol": a["daily"][1], "builtin": a.get("builtin", False)}


def _install(extra: dict[str, dict]) -> None:
    merged = copy.deepcopy(CATALOG)
    for k, v in extra.items():
        if k not in merged:
            merged[k] = v
    ASSETS.clear()
    ASSETS.update(merged)


def sync(con) -> None:
    """Registry aus Katalog + Tabelle ``markets`` aufbauen (nur wenn sich die Tabelle geändert hat)."""
    try:
        sig = con.execute("SELECT COUNT(*), COALESCE(MAX(created), 0) FROM markets").fetchone()
    except Exception:
        sig = (0, 0)
    if sig == _state["sig"] and ASSETS:
        return
    extra = {}
    if sig[0]:
        for mid, d in con.execute("SELECT id, def FROM markets"):
            try:
                m = json.loads(d)
                m["daily"] = tuple(m["daily"])
                m["intraday"] = [tuple(x) for x in m["intraday"]]
                extra[mid] = m
            except Exception:
                continue
    _install(extra)
    _state["sig"] = sig


_install({})  # Katalog sofort verfügbar (auch ohne Datenbank)


# ---------- Nutzer-Märkte (Suche) ----------
def custom_id(symbol: str) -> str:
    return "U_" + re.sub(r"[^A-Z0-9]+", "_", symbol.upper()).strip("_")[:24]


def by_symbol(symbol: str) -> str | None:
    s = symbol.upper()
    for mid, a in ASSETS.items():
        if a["daily"][1].upper() == s:
            return mid
    return None


def search(query: str, limit: int = 12) -> list[dict]:
    """Katalog zuerst (Name, Kürzel, ISIN), dann Yahoo-Suche für alles andere."""
    from . import sources

    q = query.strip()
    if len(q) < 2:
        return []
    ql = q.lower()
    out, seen = [], set()
    for mid, a in ASSETS.items():
        hay = " ".join([mid, a["name"], a.get("short", ""), a["daily"][1], a.get("isin") or ""]).lower()
        if ql in hay:
            out.append({**public(mid), "known": True})
            seen.add(a["daily"][1].upper())
    try:
        hits = sources.yahoo_search(q, limit)
    except Exception:
        hits = []
    for h in hits:
        sym = (h.get("symbol") or "").upper()
        kind = YAHOO_KIND.get(h.get("quoteType") or "")
        if not sym or sym in seen or not kind:
            continue
        seen.add(sym)
        out.append({"id": None, "symbol": sym, "name": h.get("longname") or h.get("shortname") or sym,
                    "kind": kind, "kind_label": KINDS[kind]["label"], "exchange": h.get("exchDisp") or h.get("exchange"),
                    "known": False})
    return out[:limit]


def add_custom(con, symbol: str, profile_id: int | None = None) -> dict:
    """Yahoo-Kürzel als neuen Markt anlegen: Historie prüfen, dann speichern. Gibt den Markt zurück."""
    from . import sources

    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-=^_]{1,24}", symbol):
        raise ValueError("Ungültiges Kürzel.")
    known = by_symbol(symbol)
    if known:
        return public(known)
    rows, meta = sources.yahoo_chart(symbol, "1d", period1=0, period2=int(time.time()))
    if len(rows) < 60:
        raise ValueError(f"Für {symbol} gibt es zu wenig Kursdaten ({len(rows)} Tage) – bitte ein anderes Kürzel "
                         f"(z. B. an einer anderen Börse) wählen.")
    kind = YAHOO_KIND.get(meta.get("instrumentType") or "", "etf")
    cur = meta.get("currency") or "EUR"
    name = meta.get("longName") or meta.get("shortName") or symbol
    mid = custom_id(symbol)
    _, m = _m(mid, name, kind, symbol, "", cur=cur)
    m["builtin"] = False
    if kind == "crypto":
        m["always_open"] = True
    con.execute("INSERT OR REPLACE INTO markets(id, def, added_by, created) VALUES (?,?,?,?)",
                (mid, json.dumps(m), profile_id, int(time.time())))
    con.commit()
    sync(con)
    return public(mid)
