"""Profile (du und Freunde): Einstellungen, PIN, Bestand, Sparpläne.

Jedes Profil sieht nur seine eigenen Daten. PIN ist freiwillig und wird als PBKDF2-Hash gespeichert.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import time

from . import db, markets, taxde
from .config import ASSETS, CONF_DIR, _merge, telegram_conf
from .units import (COIN_BY_ID, CURRENCIES, DEFAULT_FINENESS, INFLATION_GEOS, OZ, WEIGHT_UNITS, coin_fine_grams,
                    fine_grams)

DEFAULT_SETTINGS: dict = {
    "currency": "EUR",
    "inflation_geo": "DE",
    "tax": "de",  # de | none | flat
    "personal_rate": 0.35,  # persönlicher Steuersatz für § 23 (Gold/Silber/Bitcoin < 1 Jahr)
    "flat_rate": 0.25,
    "allowance": 1000.0,  # Sparerpauschbetrag (verheiratet 2.000 €)
    "personal_rate_auto": False,  # True = aus dem Finanzprofil (zu versteuerndes Einkommen) berechnet
    "assets": list(markets.CORE),  # beobachtete Märkte (Überblick, Bericht, Alarme)
    "gold": {"unit": "g", "fineness": 999.9},
    "silver": {"unit": "oz", "fineness": 999.0},
    "platinum": {"unit": "g", "fineness": 999.5},
    "palladium": {"unit": "g", "fineness": 999.5},
    # Finanzprofil für Ratgeber und KI-Assistent (alles freiwillig)
    "finance": {
        "income": None,  # Netto pro Monat
        "expenses": None,  # feste Ausgaben pro Monat
        "cash": None,  # Erspartes auf Konto/Tagesgeld
        "debt": None,  # teure Schulden (Dispo, Ratenkredit)
        "saving": None,  # geplante Sparrate pro Monat
        "risk": None,  # 1 (sicher) … 5 (mutig)
        "experience": None,  # none | some | much
        "age": None,
        "married": False,
        "church": "none",  # none | 8 | 9
        "zve": None,  # zu versteuerndes Jahreseinkommen
        "goals": [],  # [{id, name, purpose, amount, years}]
        "notes": "",
    },
    "ai": {"enabled": True, "mode": "global", "base_url": "", "model": "", "api": "chat", "consent": False,
           "effort": "", "max_tokens": 8000},
    "report": {
        "freq": "weekly",  # off | daily | weekly | monthly
        "weekday": 0,  # 0 = Montag
        "hour": 8,
        "content": ["holdings", "prices", "changes", "market"],
    },
    "alerts": {
        "level": "strong",  # off | strong | normal
        "rules": ["moves", "ath", "trend"],
        "max_per_day": 3,
    },
}
REPORT_CONTENT = {
    "holdings": "Wert meines Bestands",
    "prices": "Aktuelle Preise (in meiner Einheit)",
    "changes": "Veränderung seit dem letzten Bericht",
    "market": "Marktlage in einfachen Worten",
}
GOAL_PURPOSES = {
    "emergency": "Notgroschen",
    "purchase": "Größere Anschaffung (Auto, Reise, Möbel)",
    "home": "Eigenes Zuhause",
    "education": "Ausbildung / Kinder",
    "retirement": "Altersvorsorge",
    "wealth": "Vermögen aufbauen",
    "other": "Etwas anderes",
}
ALERT_RULES = {
    "moves": "Starke Kursbewegung (1 Stunde / 24 Stunden)",
    "ath": "Neues Allzeithoch",
    "trend": "Trend dreht (aufwärts ↔ abwärts)",
    "regime": "Schwankung ändert sich (ruhig ↔ wild)",
}


class ProfileError(ValueError):
    pass


# ---------- PIN ----------
def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def check_pin(pin: str, stored: str | None) -> bool:
    if not stored:
        return True
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", (pin or "").encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(test.hex(), h)


def _valid_pin(pin: str) -> str:
    pin = (pin or "").strip()
    if not (4 <= len(pin) <= 12 and pin.isdigit()):
        raise ProfileError("Die PIN muss aus 4 bis 12 Ziffern bestehen.")
    return pin


# ---------- Sitzung ----------
def _secret() -> bytes:
    path = CONF_DIR / "web_secret"
    if not path.exists():
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip().encode()


def make_session(profile_id: int) -> str:
    msg = f"{profile_id}.{int(time.time())}"
    sig = hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{msg}.{sig}"


def read_session(token: str | None, max_age_days: int = 180) -> int | None:
    if not token:
        return None
    try:
        pid, ts, sig = token.split(".")
        good = hmac.new(_secret(), f"{pid}.{ts}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(good, sig) or time.time() - int(ts) > max_age_days * 86400:
            return None
        return int(pid)
    except (ValueError, AttributeError):
        return None


# ---------- Profile ----------
def _row(r) -> dict:
    s = _merge(DEFAULT_SETTINGS, json.loads(r[3] or "{}"))
    return {"id": r[0], "name": r[1], "has_pin": bool(r[2]), "settings": s, "tg_chat_id": r[4], "created": r[5]}


def list_profiles(con) -> list[dict]:
    rows = con.execute("SELECT id, name, pin_hash, settings, tg_chat_id, created FROM profiles ORDER BY name").fetchall()
    return [_row(r) for r in rows]


def get(con, pid: int) -> dict | None:
    r = con.execute("SELECT id, name, pin_hash, settings, tg_chat_id, created FROM profiles WHERE id=?",
                    (pid,)).fetchone()
    return _row(r) if r else None


def pin_hash(con, pid: int) -> str | None:
    r = con.execute("SELECT pin_hash FROM profiles WHERE id=?", (pid,)).fetchone()
    return r[0] if r else None


def create(con, name: str, pin: str | None = None, settings: dict | None = None) -> dict:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 30:
        raise ProfileError("Der Name muss 1 bis 30 Zeichen lang sein.")
    if con.execute("SELECT 1 FROM profiles WHERE name=?", (name,)).fetchone():
        raise ProfileError("Diesen Namen gibt es schon.")
    ph = hash_pin(_valid_pin(pin)) if pin else None
    s = clean_settings(settings or {})
    cur = con.execute("INSERT INTO profiles(name, pin_hash, settings, tg_chat_id, created) VALUES (?,?,?,?,?)",
                      (name, ph, json.dumps(s), None, int(time.time())))
    con.commit()
    return get(con, cur.lastrowid)


def rename(con, pid: int, name: str) -> None:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 30:
        raise ProfileError("Der Name muss 1 bis 30 Zeichen lang sein.")
    if con.execute("SELECT 1 FROM profiles WHERE name=? AND id<>?", (name, pid)).fetchone():
        raise ProfileError("Diesen Namen gibt es schon.")
    con.execute("UPDATE profiles SET name=? WHERE id=?", (name, pid))
    con.commit()


def set_pin(con, pid: int, new_pin: str | None) -> None:
    ph = hash_pin(_valid_pin(new_pin)) if new_pin else None
    con.execute("UPDATE profiles SET pin_hash=? WHERE id=?", (ph, pid))
    con.commit()


def _num(v, cast, lo, hi, old):
    try:
        return min(max(cast(v), lo), hi)
    except (TypeError, ValueError):
        return old


def clean_settings(s: dict, base: dict | None = None) -> dict:
    """Nur bekannte Schlüssel mit gültigen Werten übernehmen; ungültige behalten den Wert aus ``base``."""
    out = copy.deepcopy(base or DEFAULT_SETTINGS)
    if s.get("currency") in CURRENCIES:
        out["currency"] = s["currency"]
    if s.get("inflation_geo") in INFLATION_GEOS:
        out["inflation_geo"] = s["inflation_geo"]
    if s.get("tax") in ("de", "none", "flat"):
        out["tax"] = s["tax"]
    for k in ("personal_rate", "flat_rate"):
        if k in s:
            out[k] = _num(s[k], float, 0.0, 0.6, out[k])
    if "allowance" in s:
        out["allowance"] = _num(s["allowance"], float, 0.0, 2000.0, out["allowance"])
    if isinstance(s.get("personal_rate_auto"), bool):
        out["personal_rate_auto"] = s["personal_rate_auto"]
    if isinstance(s.get("assets"), list):
        a = list(dict.fromkeys(x for x in s["assets"] if x in ASSETS))[:40]
        out["assets"] = a or list(markets.CORE)
    for metal in markets.METALS.values():
        m = s.get(metal) or {}
        if m.get("unit") in WEIGHT_UNITS:
            out[metal]["unit"] = m["unit"]
        if m.get("fineness"):
            out[metal]["fineness"] = _num(m["fineness"], float, 1.0, 1000.0, out[metal]["fineness"])
    r = s.get("report") or {}
    if r.get("freq") in ("off", "daily", "weekly", "monthly"):
        out["report"]["freq"] = r["freq"]
    if "weekday" in r:
        out["report"]["weekday"] = _num(r["weekday"], int, 0, 6, out["report"]["weekday"])
    if "hour" in r:
        out["report"]["hour"] = _num(r["hour"], int, 0, 23, out["report"]["hour"])
    if isinstance(r.get("content"), list):
        out["report"]["content"] = [c for c in r["content"] if c in REPORT_CONTENT]
    a = s.get("alerts") or {}
    if a.get("level") in ("off", "strong", "normal"):
        out["alerts"]["level"] = a["level"]
    if isinstance(a.get("rules"), list):
        out["alerts"]["rules"] = [c for c in a["rules"] if c in ALERT_RULES]
    if "max_per_day" in a:
        out["alerts"]["max_per_day"] = _num(a["max_per_day"], int, 1, 20, out["alerts"]["max_per_day"])
    out["finance"] = clean_finance(s.get("finance") or {}, out["finance"])
    ai = s.get("ai") or {}
    o = out["ai"]
    for k in ("enabled", "consent"):
        if isinstance(ai.get(k), bool):
            o[k] = ai[k]
    if ai.get("mode") in ("global", "own"):
        o["mode"] = ai["mode"]
    if ai.get("api") in ("chat", "responses"):
        o["api"] = ai["api"]
    for k in ("base_url", "model"):
        if isinstance(ai.get(k), str):
            o[k] = ai[k].strip()[:200]
    if ai.get("effort") in ("", "none", "minimal", "low", "medium", "high", "xhigh"):
        o["effort"] = ai["effort"]
    if "max_tokens" in ai:
        o["max_tokens"] = _num(ai["max_tokens"], int, 500, 64000, o.get("max_tokens", 8000))
    return out


def _opt_num(v, lo, hi, old):
    if v in (None, ""):
        return None
    return _num(v, float, lo, hi, old)


def clean_finance(f: dict, base: dict) -> dict:
    out = copy.deepcopy(base)
    for k, hi in (("income", 1e6), ("expenses", 1e6), ("cash", 1e9), ("debt", 1e9), ("saving", 1e6), ("zve", 1e8)):
        if k in f:
            out[k] = _opt_num(f[k], 0.0, hi, out.get(k))
    if "risk" in f:
        out["risk"] = None if f["risk"] in (None, "") else _num(f["risk"], int, 1, 5, out.get("risk"))
    if "age" in f:
        out["age"] = None if f["age"] in (None, "") else _num(f["age"], int, 14, 110, out.get("age"))
    if f.get("experience") in (None, "none", "some", "much"):
        out["experience"] = f.get("experience", out.get("experience"))
    if isinstance(f.get("married"), bool):
        out["married"] = f["married"]
    if str(f.get("church")) in ("none", "8", "9"):
        out["church"] = str(f["church"])
    if isinstance(f.get("notes"), str):
        out["notes"] = f["notes"].strip()[:1500]
    if isinstance(f.get("goals"), list):
        goals = []
        for g in f["goals"][:8]:
            if not isinstance(g, dict) or not str(g.get("name") or "").strip():
                continue
            goals.append({
                "id": str(g.get("id") or secrets.token_hex(4))[:12],
                "name": str(g["name"]).strip()[:60],
                "purpose": g.get("purpose") if g.get("purpose") in GOAL_PURPOSES else "other",
                "amount": _opt_num(g.get("amount"), 0.0, 1e9, None),
                "years": _opt_num(g.get("years"), 0.0, 60.0, None),
            })
        out["goals"] = goals
    return out


def _derive(s: dict) -> dict:
    """Aus dem Finanzprofil abgeleitete Steuer-Annahmen (Grenzsteuersatz, Sparerpauschbetrag)."""
    f = s["finance"]
    s["allowance"] = 2000.0 if f.get("married") else 1000.0
    if f.get("zve"):
        r = taxde.rates(f["zve"], bool(f.get("married")), taxde.church_rate(f.get("church")))
        s["personal_rate"] = round(min(max(r["marginal"], 0.0), 0.6), 3)
        s["personal_rate_auto"] = True
    return s


def update_settings(con, pid: int, patch: dict) -> dict:
    p = get(con, pid)
    if not p:
        raise ProfileError("Profil nicht gefunden.")
    if "finance" in patch and isinstance((patch["finance"] or {}).get("goals"), list):
        merged = _merge(p["settings"], {k: v for k, v in patch.items() if k != "finance"})
        merged["finance"] = {**p["settings"].get("finance", {}), **patch["finance"]}  # Ziele ersetzen, nicht mischen
    else:
        merged = _merge(p["settings"], patch)
    s = _derive(clean_settings(merged, base=clean_settings(p["settings"])))
    con.execute("UPDATE profiles SET settings=? WHERE id=?", (json.dumps(s), pid))
    con.commit()
    return s


def delete(con, pid: int) -> None:
    con.execute("DELETE FROM secrets WHERE scope=?", (f"profile:{pid}",))
    con.execute("DELETE FROM ai_messages WHERE profile_id=?", (pid,))
    for t in ("holdings", "plans", "tg_links", "tg_log"):
        con.execute(f"DELETE FROM {t} WHERE profile_id=?", (pid,))
    con.execute("DELETE FROM profiles WHERE id=?", (pid,))
    con.commit()


def set_chat(con, pid: int, chat_id: str | None) -> None:
    if chat_id:  # ein Chat gehört nur zu einem Profil
        con.execute("UPDATE profiles SET tg_chat_id=NULL WHERE tg_chat_id=? AND id<>?", (str(chat_id), pid))
    con.execute("UPDATE profiles SET tg_chat_id=? WHERE id=?", (str(chat_id) if chat_id else None, pid))
    con.commit()


def by_chat(con, chat_id) -> dict | None:
    r = con.execute("SELECT id FROM profiles WHERE tg_chat_id=?", (str(chat_id),)).fetchone()
    return get(con, r[0]) if r else None


def new_link_code(con, pid: int) -> str:
    code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
    con.execute("DELETE FROM tg_links WHERE profile_id=? OR created<?", (pid, int(time.time()) - 86400))
    con.execute("INSERT INTO tg_links VALUES (?,?,?)", (code, pid, int(time.time())))
    con.commit()
    return code


def redeem_link_code(con, code: str) -> int | None:
    r = con.execute("SELECT profile_id, created FROM tg_links WHERE code=?", (code,)).fetchone()
    if not r or time.time() - r[1] > 86400:
        return None
    con.execute("DELETE FROM tg_links WHERE code=?", (code,))
    con.commit()
    return r[0]


# ---------- Bestand ----------
HOLDING_FIELDS = ("asset", "form", "label", "count", "weight", "unit", "fineness", "coin", "quantity",
                  "buy_date", "buy_price", "buy_cur", "note")


def normalize_holding(h: dict) -> dict:
    """Eingabe → gespeicherte Form. quantity = Feinunzen (Metalle), BTC oder ETF-Anteile."""
    asset = h.get("asset")
    if asset not in ASSETS:
        raise ProfileError("Unbekannte Anlage.")
    out = {k: h.get(k) for k in HOLDING_FIELDS}
    out["count"] = float(h.get("count") or 1)
    if out["count"] <= 0:
        raise ProfileError("Die Anzahl muss größer als 0 sein.")
    if markets.is_metal(asset):
        form = h.get("form") or "bar"
        out["form"] = form
        if form == "coin":
            coin = h.get("coin")
            if coin not in COIN_BY_ID:
                raise ProfileError("Bitte eine Münze auswählen.")
            size = float(h.get("weight") or 1.0)
            c = COIN_BY_ID[coin]
            out["weight"] = size if c["sizes"] else 1.0
            out["unit"] = "oz" if c["sizes"] else "g"
            out["fineness"] = c["fineness"]
            out["label"] = out.get("label") or c["name"]
            fg = out["count"] * coin_fine_grams(coin, out["weight"])
        else:
            unit = h.get("unit") or "g"
            if unit not in WEIGHT_UNITS:
                raise ProfileError("Unbekannte Gewichtseinheit.")
            w = float(h.get("weight") or 0)
            f = float(h.get("fineness") or DEFAULT_FINENESS[asset])
            if w <= 0 or not 1 <= f <= 1000:
                raise ProfileError("Bitte Gewicht und Feinheit angeben.")
            out.update(weight=w, unit=unit, fineness=f, coin=None)
            fg = fine_grams(out["count"], w, unit, f)
        out["quantity"] = fg / OZ
    else:
        out["form"] = "units"
        q = float(h.get("quantity") or 0)
        if q <= 0:
            raise ProfileError("Bitte die Menge angeben.")
        out.update(quantity=q, count=1.0, weight=None, unit=None, fineness=None, coin=None)
    bd = (h.get("buy_date") or "").strip() or None
    if bd:
        try:
            time.strptime(bd, "%Y-%m-%d")
        except ValueError:
            raise ProfileError("Kaufdatum bitte als JJJJ-MM-TT.") from None
    out["buy_date"] = bd
    bp = h.get("buy_price")
    out["buy_price"] = float(bp) if bp not in (None, "") else None
    if out["buy_price"] is not None and out["buy_price"] < 0:
        raise ProfileError("Der Kaufpreis darf nicht negativ sein.")
    out["buy_cur"] = h.get("buy_cur") if h.get("buy_cur") in CURRENCIES else None
    out["label"] = (out.get("label") or "").strip()[:60] or None
    out["note"] = (out.get("note") or "").strip()[:200] or None
    return out


def list_holdings(con, pid: int) -> list[dict]:
    cols = ("id",) + HOLDING_FIELDS
    rows = con.execute(f"SELECT {', '.join(cols)} FROM holdings WHERE profile_id=? ORDER BY asset, buy_date, id",
                       (pid,)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def save_holding(con, pid: int, h: dict, hid: int | None = None) -> dict:
    n = normalize_holding(h)
    if hid:
        if not con.execute("SELECT 1 FROM holdings WHERE id=? AND profile_id=?", (hid, pid)).fetchone():
            raise ProfileError("Position nicht gefunden.")
        con.execute(f"UPDATE holdings SET {', '.join(f'{k}=?' for k in HOLDING_FIELDS)} WHERE id=? AND profile_id=?",
                    [n[k] for k in HOLDING_FIELDS] + [hid, pid])
    else:
        cur = con.execute(f"INSERT INTO holdings(profile_id, {', '.join(HOLDING_FIELDS)}, created) "
                          f"VALUES (?{', ?' * len(HOLDING_FIELDS)}, ?)",
                          [pid] + [n[k] for k in HOLDING_FIELDS] + [int(time.time())])
        hid = cur.lastrowid
    con.commit()
    return {"id": hid, **n}


def delete_holding(con, pid: int, hid: int) -> None:
    con.execute("DELETE FROM holdings WHERE id=? AND profile_id=?", (hid, pid))
    con.commit()


# ---------- Sparpläne ----------
def list_plans(con, pid: int) -> list[dict]:
    rows = con.execute("SELECT id, asset, amount, cur, start, end FROM plans WHERE profile_id=? ORDER BY start, id",
                       (pid,)).fetchall()
    return [dict(zip(("id", "asset", "amount", "cur", "start", "end"), r)) for r in rows]


def save_plan(con, pid: int, p: dict, plan_id: int | None = None) -> dict:
    if p.get("asset") not in ASSETS:
        raise ProfileError("Unbekannte Anlage.")
    amount = float(p.get("amount") or 0)
    if amount <= 0:
        raise ProfileError("Die Sparrate muss größer als 0 sein.")
    cur = p.get("cur") if p.get("cur") in CURRENCIES else "EUR"
    start, end = (p.get("start") or "").strip(), (p.get("end") or "").strip() or None
    for d in filter(None, (start, end)):
        try:
            time.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise ProfileError("Datum bitte als JJJJ-MM-TT.") from None
    if not start:
        raise ProfileError("Bitte ein Startdatum angeben.")
    vals = (p["asset"], amount, cur, start, end)
    if plan_id:
        con.execute("UPDATE plans SET asset=?, amount=?, cur=?, start=?, end=? WHERE id=? AND profile_id=?",
                    (*vals, plan_id, pid))
    else:
        plan_id = con.execute("INSERT INTO plans(profile_id, asset, amount, cur, start, end, created) "
                              "VALUES (?,?,?,?,?,?,?)", (pid, *vals, int(time.time()))).lastrowid
    con.commit()
    return {"id": plan_id, "asset": p["asset"], "amount": amount, "cur": cur, "start": start, "end": end}


def delete_plan(con, pid: int, plan_id: int) -> None:
    con.execute("DELETE FROM plans WHERE id=? AND profile_id=?", (plan_id, pid))
    con.commit()


# ---------- Admin und geheime Werte ----------
def admin_id(con) -> int | None:
    """Wer globale Einstellungen (KI-Zugang) ändern darf: das erste Profil, sofern nicht anders festgelegt."""
    v = db.kv_get(con, "admin_profile_id")
    if v and get(con, int(v)):
        return int(v)
    row = con.execute("SELECT MIN(id) FROM profiles").fetchone()
    return row[0] if row and row[0] else None


def secret_get(con, scope: str, name: str) -> str | None:
    row = con.execute("SELECT value FROM secrets WHERE scope=? AND name=?", (scope, name)).fetchone()
    return row[0] if row else None


def secret_set(con, scope: str, name: str, value: str | None) -> None:
    if value:
        con.execute("INSERT OR REPLACE INTO secrets VALUES (?,?,?)", (scope, name, value))
    else:
        con.execute("DELETE FROM secrets WHERE scope=? AND name=?", (scope, name))
    con.commit()


# ---------- Übernahme aus v1 ----------
def migrate_v1(con) -> str | None:
    """Erstes Profil für den bisherigen Nutzer anlegen und den alten Telegram-Chat übernehmen."""
    if con.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
        return None
    p = create(con, "Lndr")
    chat = telegram_conf().get("TELEGRAM_CHAT_ID")
    if chat:
        set_chat(con, p["id"], chat)
    return f"Profil Lndr angelegt{' mit Telegram-Chat' if chat else ''}."
