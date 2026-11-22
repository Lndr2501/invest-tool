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
       "Digitales Geld ohne Bank dahinter, die älteste und größte Kryptowährung. "
       "Abstürze um 70–80 % gab es mehrmals, also nur für Geld, das du lange nicht brauchst.",
       intraday=[("binance", "BTCEUR", "EUR"), ("kraken", "XBTEUR", "EUR")]),
    _m("GOLD", "Gold", "metal", "GC=F",
       "Edelmetall ohne Zinsen, das sich oft anders entwickelt als Aktien. Gut als kleine Beimischung, "
       "kann aber auch jahrelang fallen.",
       intraday=[("binance", "PAXGUSDT", "USD"), ("kraken", "PAXGEUR", "EUR")], spot="XAU", always_open=True,
       costs={"buy_fee": 0.0025, "spread": 0.005, "custody_pa": 0.003}),
    _m("SILVER", "Silber", "metal", "SI=F",
       "Edelmetall, das auch die Industrie braucht. Schwankt deutlich stärker als Gold, eher als kleine Beimischung.",
       intraday=[("yahoo", "SI=F", "USD")], spot="XAG",
       costs={"buy_fee": 0.0025, "spread": 0.01, "custody_pa": 0.002}),
    _m("MSCIW", "MSCI World (ETF)", "etf", "EUNL.DE",
       "ETF, also ein Korb mit rund 1.400 großen Firmen aus 23 Industrieländern. Der Klassiker für Einsteiger zum "
       "langen Sparen, kann in Krisen aber um 30–50 % fallen.",
       short="MSCI World", isin="IE00B4L5Y983", costs={"buy_fee": 0.001, "spread": 0.002, "custody_pa": 0.002}),
    # --- Krypto ---
    _crypto("ETH", "Ethereum", "Zweitgrößte Kryptowährung und Plattform für digitale Anwendungen. "
                               "Schwankt noch stärker als Bitcoin, nur für kleine Beträge."),
    _crypto("SOL", "Solana", "Junge Kryptowährung für digitale Anwendungen (seit 2020). Sehr riskant, nur für Geld, dessen Verlust "
                             "du verkraften kannst."),
    _crypto("XRP", "XRP", "Kryptowährung für schnelle Zahlungen zwischen Banken. Sehr riskant, nur für Geld, "
                          "dessen Verlust du verkraften kannst."),
    _crypto("ADA", "Cardano", "Kryptowährung für digitale Anwendungen, aus der Forschung entstanden. Sehr riskant, nur für Geld, "
                              "dessen Verlust du verkraften kannst."),
    _crypto("DOGE", "Dogecoin", "Als Spaß gestartete Kryptowährung, der Kurs hängt an Stimmung und Prominenten. "
                                "Eher eine Wette als eine Geldanlage."),
    _crypto("LTC", "Litecoin", "Eine der ältesten Kryptowährungen, technisch ähnlich wie Bitcoin. "
                               "Sehr starke Schwankungen, nur für kleine Beträge."),
    _crypto("LINK", "Chainlink", "Dienst, der Krypto-Anwendungen mit Daten von außen versorgt. "
                                 "Sehr riskant, nur für Geld, dessen Verlust du verkraften kannst."),
    _crypto("BNB", "BNB", "Kryptowährung der Handelsplattform Binance, hängt am Erfolg dieser einen Firma. "
                          "Sehr riskant, nur für kleine Beträge."),
    # --- Metalle ---
    _m("PLATINUM", "Platin", "metal", "PL=F",
       "Seltenes Edelmetall, vor allem für Auto-Katalysatoren und Schmuck gefragt. Schwankt stärker als Gold, "
       "eher etwas für Kenner.",
       intraday=[("yahoo", "PL=F", "USD")], spot="XPT"),
    _m("PALLADIUM", "Palladium", "metal", "PA=F",
       "Edelmetall, das vor allem in Auto-Katalysatoren steckt. Der Preis springt stark nach oben und unten, "
       "eher etwas für Kenner.",
       intraday=[("yahoo", "PA=F", "USD")], spot="XPD"),
    # --- ETFs ---
    _m("FTSEAW", "FTSE All-World (ETF)", "etf", "VWCE.DE",
       "ETF-Korb mit rund 4.000 Firmen aus Industrieländern und aufstrebenden Ländern wie China oder Indien. "
       "Gut zum langen Sparen, schwankt ähnlich wie der MSCI World.", short="FTSE All-World",
       isin="IE00BK5BQT80"),
    _m("ACWI", "MSCI ACWI IMI (ETF)", "etf", "SPYI.DE",
       "ETF-Korb mit fast allen Aktien der Welt, auch kleinen Firmen und aufstrebenden Ländern. "
       "Gut zum langen Sparen, schwankt ähnlich wie der MSCI World.", short="MSCI ACWI IMI",
       isin="IE00B3YLTY66"),
    _m("SP500", "S&P 500 (ETF)", "etf", "SXR8.DE",
       "ETF-Korb mit den 500 größten Firmen der USA. Gut zum langen Sparen, "
       "setzt aber alles auf ein Land.", short="S&P 500",
       isin="IE00B5BMR087"),
    _m("NDX", "Nasdaq 100 (ETF)", "etf", "SXRV.DE",
       "ETF-Korb mit den 100 größten Firmen der US-Börse Nasdaq, vor allem Technik. Schwankt stärker als "
       "der breite Markt, eher als Beimischung.",
       short="Nasdaq 100", isin="IE00B53SZB19"),
    _m("EM", "Schwellenländer (ETF)", "etf", "IS3N.DE",
       "ETF-Korb mit Firmen aus aufstrebenden Ländern (Schwellenländern) wie China, Indien und Brasilien. "
       "Schwankt stärker als der MSCI World, eher als Beimischung.",
       short="Schwellenländer", isin="IE00BKM4GZ66"),
    _m("DAX", "DAX (ETF)", "etf", "EXS1.DE", "ETF-Korb mit den 40 größten deutschen Firmen. Setzt alles auf "
       "Deutschland, eher als Ergänzung zu einem Welt-ETF.", short="DAX", isin="DE0005933931"),
    _m("STOXX600", "Europa – STOXX 600 (ETF)", "etf", "EXSA.DE",
       "ETF-Korb mit 600 Firmen aus ganz Europa. Eher als Ergänzung zu einem Welt-ETF, schwankt wie Aktien insgesamt.",
       short="STOXX 600",
       isin="DE0002635307", adjusted=True),
    _m("EUGOV", "Euro-Staatsanleihen (ETF)", "bond", "EUNH.DE",
       "ETF mit Staatsanleihen aus dem Euroraum, also Krediten an Staaten, die Zinsen bringen. "
       "Ruhiges Gegengewicht zu Aktien, kann bei steigenden Zinsen aber spürbar fallen.",
       short="Euro-Staatsanleihen", isin="IE00B4WXJJ64"),
    # --- Fonds ---
    _m("FUNDSMITH", "Fundsmith Equity (Fonds)", "fund", "0P0000RU81.L",
       "Aktienfonds, bei dem ein Manager rund 30 Firmen selbst auswählt. Kostet jedes Jahr deutlich mehr als ein ETF "
       "und schwankt wie Aktien.",
       short="Fundsmith Equity", isin="LU0690375182", cur="GBP"),
    # --- Aktien ---
    _m("AAPL", "Apple", "stock", "AAPL",
       "Aktie (Firmenanteil) des iPhone-Herstellers, einer der wertvollsten Firmen der Welt. "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung.", cur="USD"),
    _m("MSFT", "Microsoft", "stock", "MSFT",
       "Aktie des Software-Konzerns hinter Windows und Office, dazu Rechenzentren zum Mieten (Cloud). "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung.", cur="USD"),
    _m("NVDA", "Nvidia", "stock", "NVDA", "Aktie des Herstellers von Grafik- und KI-Chips. Stark gestiegen und deshalb "
       "anfällig für starke Rückschläge.",
       cur="USD"),
    _m("AMZN", "Amazon", "stock", "AMZN",
       "Aktie des Onlinehändlers, der auch Rechenzentren zum Mieten (AWS) anbietet. "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung.", cur="USD"),
    _m("GOOGL", "Alphabet (Google)", "stock", "GOOGL", "Aktie der Google-Mutter: Suche, YouTube und Werbung. "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung.", short="Alphabet", cur="USD"),
    _m("TSLA", "Tesla", "stock", "TSLA",
       "Aktie des Elektroauto-Herstellers. Eine der am stärksten schwankenden großen Aktien, "
       "nur für Geld, das du lange nicht brauchst.", cur="USD"),
    _m("SAP", "SAP", "stock", "SAP.DE", "Aktie des größten deutschen Softwarekonzerns. "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung."),
    _m("SIE", "Siemens", "stock", "SIE.DE",
       "Aktie des Industriekonzerns für Fabrik-Automatisierung, Bahn- und Gebäudetechnik. "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung."),
    _m("ALV", "Allianz", "stock", "ALV.DE",
       "Aktie des Versicherungskonzerns, zahlt hohe Dividenden (Gewinnanteile an die Aktionäre). "
       "Als einzelne Aktie riskanter als ein ETF-Korb, eher als kleine Beimischung."),
    _m("NESN", "Nestlé", "stock", "NESN.SW", "Aktie des Lebensmittelkonzerns (Nescafé, Maggi). Gilt als eher ruhig, "
       "ist als einzelne Aktie aber riskanter als ein ETF-Korb.", cur="CHF"),
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
