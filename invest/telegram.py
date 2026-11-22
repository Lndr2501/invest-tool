"""Telegram pro Profil: Versand, Bericht, Bot (Long-Polling) mit Verknüpfung per Code.

Verknüpfen: Website → Einstellungen → „Telegram verbinden“ öffnet t.me/<bot>?start=<code>.
Der Bot ordnet den Chat dem Profil zu. Befehle: /bericht /preis /bestand /stop /hilfe
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import time
from zoneinfo import ZoneInfo

import requests

from . import db, profiles, service
from .config import ASSETS, telegram_conf
from .units import fmt_money, fmt_num

log = logging.getLogger("invest.telegram")
TZ = ZoneInfo("Europe/Berlin")
WEB_URL = "http://192.168.178.72:8501"
PERIOD = {"daily": ("24h", "24 Std."), "weekly": ("7d", "7 Tage"), "monthly": ("30d", "30 Tage"), "off": ("7d", "7 Tage")}
TITLE = {"daily": "☀️ Dein Tagesbericht", "weekly": "📊 Dein Wochenbericht", "monthly": "🗓 Dein Monatsbericht",
         "off": "📊 Dein Bericht"}
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _token() -> str | None:
    return telegram_conf().get("TELEGRAM_TOKEN")


def api(method: str, http_timeout: float = 15, **params) -> dict:
    tok = _token()
    if not tok:
        return {"ok": False, "description": "kein Token"}
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/{method}", json=params, timeout=http_timeout)
        return r.json()
    except Exception as e:  # Fehlermeldung ohne URL (enthält den Token)
        return {"ok": False, "description": type(e).__name__}


def send(chat_id, text: str) -> bool:
    if not chat_id:
        return False
    j = api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    if not j.get("ok"):
        log.warning("Telegram-Versand an Chat fehlgeschlagen: %s", j.get("description"))
    return bool(j.get("ok"))


def bot_username(con=None) -> str | None:
    own = con is None
    con = con or db.connect()
    name = db.kv_get(con, "tg:bot_username")
    if not name and _token():
        j = api("getMe")
        if j.get("ok"):
            name = j["result"].get("username")
            db.kv_set(con, "tg:bot_username", name)
            con.commit()
    if own:
        con.close()
    return name


def log_send(con, pid: int, kind: str, key: str, text: str, sent: bool) -> None:
    con.execute("INSERT INTO tg_log(profile_id, ts, kind, key, text, sent) VALUES (?,?,?,?,?,?)",
                (pid, int(time.time()), kind, key, text, int(sent)))
    con.execute("DELETE FROM tg_log WHERE ts<?", (int(time.time()) - 120 * 86400,))
    con.commit()


# ---------- Texte ----------
def _chg(x: float | None) -> str:
    if x is None:
        return "–"
    return f"{'+' if x >= 0 else '−'}{fmt_num(abs(x) * 100, 1)} %"


def price_lines(con, profile: dict, period_key: str = "24h", period_label: str = "24 Std.") -> list[str]:
    out = []
    for a in [x for x in profile["settings"]["assets"] if x in ASSETS][:12]:
        try:
            p = service.price_display(con, profile, a)
        except Exception:
            continue
        if p["value"] is None:
            continue
        unit = p["label"].replace("1 Gramm ", "1 g ").replace("1 Unze ", "1 oz ").replace("1 Kilo ", "1 kg ")
        out.append(f"<b>{unit}</b>: {p['text']}  ({period_label} {_chg(p['change'].get(period_key))})")
    return out


def holdings_lines(con, profile: dict, period_key: str, period_label: str, with_change: bool) -> list[str]:
    pf = service.portfolio(con, profile)
    if pf["empty"]:
        return []
    cur = pf["currency"]
    line = f"💼 <b>Dein Bestand: {fmt_money(pf['total'], cur)}</b>"
    ch = pf["change"].get(period_key)
    if with_change and ch:
        line += f"\n{period_label}: {_chg(ch['pct'])} ({'+' if ch['abs'] >= 0 else '−'}{fmt_money(abs(ch['abs']), cur)})"
    if pf["gain"] is not None:
        line += (f"\nSeit Kauf: {'+' if pf['gain'] >= 0 else '−'}{fmt_money(abs(pf['gain']), cur)} "
                 f"({_chg(pf['gain_pct'])})")
    return [line]


def market_lines(con, profile: dict) -> list[str]:
    out = []
    for a in [x for x in profile["settings"]["assets"] if x in ASSETS][:8]:
        ana = db.kv_get(con, f"analysis:{a}") or {}
        t = (ana.get("trend") or {}).get("label")
        v = (ana.get("vol") or {}).get("label")
        if t:
            out.append(f"• {ASSETS[a]['name']} {service.TREND_TEXT.get(t, t)}; Kurse {service.VOL_TEXT.get(v, v)}.")
    return out


def report_text(con, profile: dict) -> str:
    s = profile["settings"]
    freq = s["report"]["freq"]
    key, label = PERIOD[freq]
    content = s["report"]["content"]
    parts = [f"{TITLE[freq]} · {dt.datetime.now(TZ).strftime('%d.%m.%Y')}"]
    if "holdings" in content:
        h = holdings_lines(con, profile, key, label, "changes" in content)
        if h:
            parts.append("\n".join(h))
    if "prices" in content:
        lines = price_lines(con, profile, key, label) if "changes" in content else \
            [ln.split("  (")[0] for ln in price_lines(con, profile, key, label)]
        parts.append("\n".join(lines))
    if "market" in content:
        m = market_lines(con, profile)
        if m:
            parts.append("🧭 <b>Marktlage</b>\n" + "\n".join(m))
    parts.append(f"<i>Preise = reiner Materialwert ohne Händleraufschlag. Einstellungen: {WEB_URL}</i>")
    return "\n\n".join(parts)


def help_text(linked: bool) -> str:
    if not linked:
        return ("👋 Dieser Chat ist noch mit keinem Profil verbunden.\n\nÖffne die Website "
                f"{WEB_URL} → <b>Einstellungen</b> → <b>Telegram verbinden</b>.")
    return ("<b>Befehle</b>\n/bericht – Bericht jetzt schicken\n/preis – aktuelle Preise\n"
            "/bestand – Wert deines Bestands\n/stop – Telegram trennen\n/hilfe – diese Hilfe\n\n"
            f"Was und wie oft du Nachrichten bekommst, stellst du hier ein: {WEB_URL}/#/einstellungen")


def schedule_text(s: dict) -> str:
    r = s["report"]
    if r["freq"] == "off":
        return "Bericht: aus"
    when = {"daily": "jeden Tag", "weekly": f"jeden {WEEKDAYS[r['weekday']]}", "monthly": "am 1. jedes Monats"}[r["freq"]]
    lvl = {"off": "keine Alarme", "strong": "Alarme nur bei sehr großen Bewegungen",
           "normal": "Alarme bei großen Bewegungen"}[s["alerts"]["level"]]
    return f"Bericht {when} um {r['hour']} Uhr · {lvl}"


# ---------- Bot ----------
def handle(con, msg: dict) -> None:
    chat = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat or not text.startswith("/"):
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    prof = profiles.by_chat(con, chat)
    if cmd == "/start" and arg.strip():
        pid = profiles.redeem_link_code(con, arg.strip())
        if not pid:
            send(chat, "❌ Der Code ist ungültig oder abgelaufen. Erzeuge auf der Website einen neuen.")
            return
        profiles.set_chat(con, pid, chat)
        p = profiles.get(con, pid)
        send(chat, f"✅ Verbunden mit dem Profil <b>{html.escape(p['name'])}</b>.\n{schedule_text(p['settings'])}.\n\n"
                   + help_text(True))
        return
    if not prof:
        send(chat, help_text(False))
        return
    if cmd in ("/bericht", "/report"):
        t = report_text(con, prof)
        log_send(con, prof["id"], "report_manual", dt.date.today().isoformat(), t, send(chat, t))
    elif cmd in ("/preis", "/preise"):
        send(chat, "💰 <b>Aktuelle Preise</b>\n" + "\n".join(price_lines(con, prof)))
    elif cmd == "/bestand":
        h = holdings_lines(con, prof, "24h", "24 Std.", True)
        send(chat, h[0] if h else f"Noch kein Bestand eingetragen: {WEB_URL}/#/bestand")
    elif cmd == "/stop":
        profiles.set_chat(con, prof["id"], None)
        send(chat, "👋 Getrennt. Du bekommst keine Nachrichten mehr. Neu verbinden über die Website.")
    else:
        send(chat, f"Profil <b>{html.escape(prof['name'])}</b> · {schedule_text(prof['settings'])}\n\n" + help_text(True))


def run_bot() -> None:
    """Endlosschleife (systemd invest-bot.service)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not _token():
        raise SystemExit("Kein TELEGRAM_TOKEN konfiguriert.")
    con = db.connect()
    from . import profiles as _p
    _p.migrate_v1(con)
    bot_username(con)
    api("setMyCommands", commands=[
        {"command": "bericht", "description": "Bericht jetzt schicken"},
        {"command": "preis", "description": "Aktuelle Preise"},
        {"command": "bestand", "description": "Wert deines Bestands"},
        {"command": "hilfe", "description": "Hilfe und Einstellungen"},
        {"command": "stop", "description": "Telegram trennen"}])
    offset = db.kv_get(con, "tg:offset", 0)
    log.info("Bot läuft")
    while True:
        j = api("getUpdates", http_timeout=70, timeout=50, offset=offset, allowed_updates=["message"])
        if not j.get("ok"):
            time.sleep(10)
            continue
        for u in j.get("result", []):
            offset = u["update_id"] + 1
            try:
                if u.get("message"):
                    handle(con, u["message"])
            except Exception:
                log.exception("Nachricht konnte nicht verarbeitet werden")
        db.kv_set(con, "tg:offset", offset)
        con.commit()
