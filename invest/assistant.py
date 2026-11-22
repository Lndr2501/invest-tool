"""KI-Assistent: OpenAI-kompatibel, wahlweise Chat Completions (/chat/completions) oder Responses API (/responses).

* Zugang: global (vom Admin-Profil eingestellt) oder pro Profil ein eigener. API-Keys liegen in der Tabelle
  ``secrets`` und verlassen den Server nie Richtung Browser. Ändert sich die Adresse, wird der Key gelöscht.
* Daten-Schutz: Liegt der Server nicht im Heimnetz, braucht es die Zustimmung des Profils (``ai.consent``).
* Werkzeuge (Function Calling): Überblick/Bestand, Märkte, Rechner, Finanzprofil speichern, Markt beobachten.
  Kann ein Server keine Werkzeuge, antwortet der Assistent nur mit der Kurzfassung im System-Prompt.
* Ausgabe als Ereignisse: {"type": "status"|"delta"|"saved"|"done"|"error", …} – die Website streamt sie per SSE.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import re
import socket
import time
from typing import Iterator
from urllib.parse import urlparse

import requests

from . import calcs, db, markets, profiles, service
from .config import ASSETS
from .units import fmt_money, fmt_num

log = logging.getLogger("invest.assistant")
MAX_ROUNDS = 6
HISTORY = 20
TOOL_RESULT_CHARS = 7000


class AIError(RuntimeError):
    pass


# ---------- Konfiguration ----------
DEFAULT_GLOBAL = {"enabled": False, "base_url": "", "model": "", "api": "chat", "effort": "", "max_tokens": 8000}
# Denktiefe für Reasoning-Modelle; "" = nichts senden (Standard des Servers)
EFFORTS = ("", "none", "minimal", "low", "medium", "high", "xhigh")
MAX_TOKENS = (500, 64000)


def clean_tuning(patch: dict, into: dict) -> None:
    """effort/max_tokens prüfen und übernehmen (gemeinsamer und eigener Zugang)."""
    if patch.get("effort") in EFFORTS:
        into["effort"] = patch["effort"]
    if patch.get("max_tokens") not in (None, ""):
        try:
            into["max_tokens"] = int(min(max(int(float(patch["max_tokens"])), MAX_TOKENS[0]), MAX_TOKENS[1]))
        except (TypeError, ValueError):
            raise AIError("Die maximale Antwortlänge muss eine Zahl sein, zum Beispiel 8000.") from None


def global_conf(con) -> dict:
    return {**DEFAULT_GLOBAL, **(db.kv_get(con, "ai:global") or {})}


def set_global(con, patch: dict) -> dict:
    old = global_conf(con)
    new = dict(old)
    for k in ("base_url", "model"):
        if isinstance(patch.get(k), str):
            new[k] = patch[k].strip()[:200]
    if patch.get("api") in ("chat", "responses"):
        new["api"] = patch["api"]
    if isinstance(patch.get("enabled"), bool):
        new["enabled"] = patch["enabled"]
    clean_tuning(patch, new)
    if new["base_url"] and not re.match(r"^https?://", new["base_url"]):
        raise AIError("Die Adresse muss mit http:// oder https:// beginnen, zum Beispiel http://192.168.1.20:8000/v1.")
    if new["base_url"] != old["base_url"]:
        profiles.secret_set(con, "global", "ai_key", None)  # Key nie an eine neue Adresse mitschicken
    if patch.get("key"):
        profiles.secret_set(con, "global", "ai_key", patch["key"].strip())
    if patch.get("clear_key"):
        profiles.secret_set(con, "global", "ai_key", None)
    db.kv_set(con, "ai:global", new)
    con.commit()
    return new


def set_own(con, profile: dict, patch: dict) -> dict:
    """Eigenen Zugang/Einstellungen eines Profils ändern. Key nur hier, nie über die normalen Einstellungen."""
    pid = profile["id"]
    old = profile["settings"].get("ai") or {}
    ai = {k: patch[k] for k in ("enabled", "consent", "mode", "api", "base_url", "model") if k in patch}
    clean_tuning(patch, ai)
    if isinstance(ai.get("base_url"), str) and ai["base_url"].strip() and not re.match(r"^https?://", ai["base_url"].strip()):
        raise AIError("Die Adresse muss mit http:// oder https:// beginnen, zum Beispiel http://192.168.1.20:8000/v1.")
    s = profiles.update_settings(con, pid, {"ai": ai})
    if s["ai"]["base_url"] != old.get("base_url", ""):
        profiles.secret_set(con, f"profile:{pid}", "ai_key", None)
    if patch.get("key"):
        profiles.secret_set(con, f"profile:{pid}", "ai_key", str(patch["key"]).strip())
    if patch.get("clear_key"):
        profiles.secret_set(con, f"profile:{pid}", "ai_key", None)
    profile["settings"] = s
    return s["ai"]


def is_local(url: str) -> bool:
    """Liegt der Server im eigenen Netz (privat/loopback)? Dann bleiben die Daten zu Hause."""
    host = urlparse(url).hostname or ""
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".lan") or host.endswith(".home"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    try:
        return all(ipaddress.ip_address(i[4][0]).is_private or ipaddress.ip_address(i[4][0]).is_loopback for i in infos)
    except ValueError:
        return False


def effective(con, profile: dict) -> dict:
    """Welcher Zugang gilt für dieses Profil – und darf er benutzt werden?"""
    own = profile["settings"].get("ai") or {}
    if own.get("mode") == "own":
        c = {"base_url": own.get("base_url", ""), "model": own.get("model", ""), "api": own.get("api", "chat"),
             "effort": own.get("effort", ""), "max_tokens": own.get("max_tokens", DEFAULT_GLOBAL["max_tokens"]),
             "key": profiles.secret_get(con, f"profile:{profile['id']}", "ai_key"), "scope": "own", "enabled": True}
    else:
        g = global_conf(con)
        c = {**g, "key": profiles.secret_get(con, "global", "ai_key"), "scope": "global"}
    c["local"] = bool(c["base_url"]) and is_local(c["base_url"])
    reason = None
    if not own.get("enabled", True):
        reason = "Der KI-Ratgeber ist bei dir ausgeschaltet. Du kannst ihn unter Einstellungen → KI-Ratgeber einschalten."
    elif not c["base_url"] or not c["model"]:
        reason = ("Der KI-Ratgeber ist noch nicht eingerichtet." + (" Das erledigt der Admin (wer die App verwaltet) "
                  "unter Einstellungen → KI-Ratgeber." if c["scope"] == "global"
                  else " Trag dafür unter Einstellungen → KI-Ratgeber die Adresse und das Modell ein."))
    elif c["scope"] == "global" and not c.get("enabled"):
        reason = "Der gemeinsame KI-Zugang ist gerade ausgeschaltet. Nur der Admin kann ihn wieder einschalten."
    elif not c["local"] and not own.get("consent"):
        reason = (f"Der KI-Server ({urlparse(c['base_url']).hostname}) steht nicht in deinem Heimnetz. Deine Daten "
                  "(Bestand, Finanzprofil) würden also dorthin geschickt. Wenn das für dich in Ordnung ist, "
                  "musst du erst unter Einstellungen → KI-Ratgeber zustimmen.")
    c["ready"] = reason is None
    c["reason"] = reason
    return c


def public_conf(con, profile: dict) -> dict:
    g = global_conf(con)
    e = effective(con, profile)
    own = profile["settings"].get("ai") or {}
    return {"global": {**g, "has_key": bool(profiles.secret_get(con, "global", "ai_key")),
                       "local": bool(g["base_url"]) and is_local(g["base_url"])},
            "own": {**own, "has_key": bool(profiles.secret_get(con, f"profile:{profile['id']}", "ai_key"))},
            "is_admin": profiles.admin_id(con) == profile["id"],
            "effective": {k: e.get(k) for k in ("scope", "base_url", "model", "api", "effort", "max_tokens", "local",
                                                 "ready", "reason")}}


# ---------- HTTP ----------
def _headers(c: dict) -> dict:
    h = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
    if c.get("key"):
        h["Authorization"] = f"Bearer {c['key']}"
    return h


def _url(c: dict, path: str) -> str:
    return c["base_url"].rstrip("/") + path


def list_models(c: dict) -> list[str]:
    r = requests.get(_url(c, "/models"), headers=_headers(c), timeout=10)
    if r.status_code != 200:
        raise AIError(f"/models: HTTP {r.status_code} {r.text[:200]}")
    data = r.json().get("data") or []
    return sorted(m.get("id") for m in data if m.get("id"))


def test(c: dict) -> dict:
    """Verbindung prüfen: Modelle, eine kurze Antwort, Werkzeug-Aufruf."""
    out = {"ok": False, "models": [], "api": c.get("api"), "tools": None, "ms": None, "error": None}
    try:
        out["models"] = list_models(c)
    except Exception as e:
        out["error"] = str(e)[:300]
    t0 = time.time()
    try:
        tool = [{"name": "ping", "description": "Antwortet mit pong.", "parameters": {"type": "object", "properties": {}}}]
        events = list(_stream_round(c, "Du bist ein Test. Rufe das Werkzeug ping auf.",
                                    [{"role": "user", "content": "Bitte rufe ping auf."}], tool,
                                    max_tokens=min(int(c.get("max_tokens") or 2000), 2000)))
        calls = [e for e in events if e[0] == "call"]
        out["tools"] = bool(calls)
        out["ok"] = True
        out["error"] = None
    except Exception as e:
        out["error"] = str(e)[:300]
    out["ms"] = int((time.time() - t0) * 1000)
    return out


def _tool_specs(api: str, tools: list[dict]) -> list[dict]:
    if api == "responses":
        return [{"type": "function", **t} for t in tools]
    return [{"type": "function", "function": t} for t in tools]


class _ThinkFilter:
    """Blendet <think>…</think> aus gestreamtem Text aus (lokale Reasoning-Modelle)."""

    def __init__(self):
        self.inside = False
        self.buf = ""

    def feed(self, s: str) -> str:
        self.buf += s
        out = ""
        while self.buf:
            if self.inside:
                i = self.buf.find("</think>")
                if i < 0:
                    self.buf = self.buf[-8:]
                    return out
                self.buf = self.buf[i + 8:]
                self.inside = False
            else:
                i = self.buf.find("<think>")
                if i < 0:
                    keep = max(0, len(self.buf) - 7)  # evtl. angefangenes Tag zurückhalten
                    out += self.buf[:keep]
                    self.buf = self.buf[keep:]
                    return out
                out += self.buf[:i]
                self.buf = self.buf[i + 7:]
                self.inside = True
        return out

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


def _sse(resp) -> Iterator[dict]:
    resp.encoding = "utf-8"
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _post(c: dict, path: str, body: dict):
    r = requests.post(_url(c, path), headers=_headers(c), json=body, stream=True, timeout=(10, 180))
    if r.status_code != 200:
        txt = r.text[:400]
        r.close()
        raise AIError(f"HTTP {r.status_code}: {txt}")
    return r


def _post_tuned(c: dict, path: str, body: dict):
    """Anfrage mit Denktiefe; lehnt der Server das Feld ab, einmal ohne wiederholen."""
    eff = c.get("effort") or ""
    if eff:
        if path == "/responses":
            body["reasoning"] = {"effort": eff}
        else:
            body["reasoning_effort"] = eff
    try:
        return _post(c, path, body)
    except AIError as e:
        if eff and "reason" in str(e).lower():
            body.pop("reasoning", None)
            body.pop("reasoning_effort", None)
            log.info("KI-Server lehnt Denktiefe ab, ohne weiter: %s", str(e)[:120])
            return _post(c, path, body)
        raise


def _stream_round(c: dict, system: str, messages: list, tools: list[dict] | None, max_tokens: int | None = None,
                  extra_input: list | None = None):
    """Eine Anfrage. Liefert ("text", s) für Text und am Ende ("call", {id, name, arguments}) je Werkzeug-Aufruf
    sowie ("raw", item) für Responses-Ausgabe-Elemente, die bei der nächsten Runde mitgeschickt werden."""
    think = _ThinkFilter()
    max_tokens = int(max_tokens or c.get("max_tokens") or DEFAULT_GLOBAL["max_tokens"])
    if c["api"] == "responses":
        inp = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        inp += extra_input or []
        body = {"model": c["model"], "instructions": system, "input": inp, "stream": True, "store": False,
                "max_output_tokens": max_tokens}
        if tools:
            body["tools"] = _tool_specs("responses", tools)
        resp = _post_tuned(c, "/responses", body)
        calls = []
        try:
            for ev in _sse(resp):
                t = ev.get("type", "")
                if t == "response.output_text.delta":
                    s = think.feed(ev.get("delta") or "")
                    if s:
                        yield ("text", s)
                elif t == "response.output_item.done":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        calls.append(item)
                elif t in ("response.failed", "error"):
                    err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
                    raise AIError(f"Antwort fehlgeschlagen: {str(err)[:300]}")
        finally:
            resp.close()
        rest = think.flush()
        if rest:
            yield ("text", rest)
        for it in calls:
            yield ("raw", {"type": "function_call", "call_id": it.get("call_id"), "name": it.get("name"),
                           "arguments": it.get("arguments") or "{}"})
            yield ("call", {"id": it.get("call_id"), "name": it.get("name"), "arguments": it.get("arguments") or "{}"})
        return
    msgs = [{"role": "system", "content": system}] + messages
    body = {"model": c["model"], "messages": msgs, "stream": True, "max_tokens": max_tokens}
    if tools:
        body["tools"] = _tool_specs("chat", tools)
    resp = _post_tuned(c, "/chat/completions", body)
    acc: dict[int, dict] = {}
    try:
        for ev in _sse(resp):
            if ev.get("error"):
                raise AIError(str(ev["error"])[:300])
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    s = think.feed(d["content"])
                    if s:
                        yield ("text", s)
                for tc in d.get("tool_calls") or []:
                    k = tc.get("index", 0)
                    a = acc.setdefault(k, {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        a["id"] = tc["id"]
                    f = tc.get("function") or {}
                    if f.get("name"):
                        a["name"] += f["name"]
                    if f.get("arguments"):
                        a["arguments"] += f["arguments"]
    finally:
        resp.close()
    rest = think.flush()
    if rest:
        yield ("text", rest)
    for k in sorted(acc):
        a = acc[k]
        yield ("call", {"id": a["id"] or f"call_{k}", "name": a["name"], "arguments": a["arguments"] or "{}"})


# ---------- Werkzeuge ----------
TOOLS = [
    {"name": "get_overview", "description": "Aktueller Stand des Profils: Bestand, Sparpläne, beobachtete Märkte mit "
     "Preisen, Finanzprofil (Einkommen, Ziele, Risiko, Steuern) und Einstellungen.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_market", "description": "Details zu einem Markt: Preis in der Einheit des Profils, Veränderungen, "
     "Trend, Schwankung, größte Abstürze, Jahresrenditen. asset = Markt-ID (z. B. BTC, GOLD, MSCIW, SP500, ETH).",
     "parameters": {"type": "object", "properties": {"asset": {"type": "string"}}, "required": ["asset"]}},
    {"name": "search_markets", "description": "Märkte suchen (Name, Kürzel oder ISIN). Liefert Markt-IDs.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "list_calculators", "description": "Alle Rechner mit ihren Eingabefeldern (key, Typ, Standardwert).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "run_calculator", "description": "Einen Rechner ausführen. id aus list_calculators, inputs = Feldwerte "
     "(Prozentfelder in Prozent, z. B. 5 für 5 %). Für future/goal/past: weights = {Markt-ID: Prozent}.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "inputs": {"type": "object"}},
                    "required": ["id"]}},
    {"name": "update_finance_profile", "description": "Angaben der Person dauerhaft speichern. Felder: income "
     "(Netto/Monat), expenses (feste Ausgaben/Monat), cash (Erspartes), debt (teure Schulden), saving (Sparrate/Monat), "
     "risk (1 sicher … 5 mutig), experience (none|some|much), age, married (bool), church (none|8|9), zve (zu "
     "versteuerndes Jahreseinkommen), goals (vollständige Liste: [{name, purpose: emergency|purchase|home|education|"
     "retirement|wealth|other, amount, years}]), notes (kurze Notiz). Nur Felder angeben, die sich ändern.",
     "parameters": {"type": "object", "properties": {"fields": {"type": "object"}}, "required": ["fields"]}},
    {"name": "watch_market", "description": "Markt zur Beobachtungsliste hinzufügen (watch=true) oder entfernen.",
     "parameters": {"type": "object", "properties": {"asset": {"type": "string"}, "watch": {"type": "boolean"}},
                    "required": ["asset", "watch"]}},
]
STATUS = {"get_overview": "Ich schaue in deinen Bestand …", "get_market": "Ich schaue mir die Kurse an …",
          "search_markets": "Ich suche passende Anlagen …", "list_calculators": "Ich suche den passenden Rechner …",
          "run_calculator": "Ich rechne das durch …", "update_finance_profile": "Ich merke mir das …",
          "watch_market": "Ich passe deine beobachteten Märkte an …"}


def _compact(o, limit=TOOL_RESULT_CHARS) -> str:
    s = json.dumps(o, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…(gekürzt)"


def finance_text(profile: dict) -> str:
    s = profile["settings"]
    f = s.get("finance") or {}
    cur = s["currency"]
    parts = []
    for k, label in (("income", "Netto/Monat"), ("expenses", "feste Ausgaben/Monat"), ("cash", "Erspartes"),
                     ("debt", "teure Schulden"), ("saving", "Sparrate/Monat")):
        if f.get(k) is not None:
            parts.append(f"{label} {fmt_money(f[k], cur)}")
    if f.get("risk"):
        parts.append(f"Risiko {f['risk']}/5")
    if f.get("experience"):
        parts.append({"none": "keine Erfahrung", "some": "etwas Erfahrung", "much": "viel Erfahrung"}[f["experience"]])
    if f.get("age"):
        parts.append(f"Alter {f['age']}")
    if f.get("zve"):
        parts.append(f"zvE {fmt_money(f['zve'], cur)} → Grenzsteuersatz {fmt_num(s['personal_rate'] * 100, 0)} %")
    parts.append("verheiratet" if f.get("married") else "nicht verheiratet/zusammen veranlagt")
    if f.get("church") and f["church"] != "none":
        parts.append(f"Kirchensteuer {f['church']} %")
    goals = [f"„{g['name']}“ ({profiles.GOAL_PURPOSES.get(g['purpose'], g['purpose'])}"
             + (f", {fmt_money(g['amount'], cur)}" if g.get("amount") else "")
             + (f", in {fmt_num(g['years'], 0)} J." if g.get("years") else "") + ")" for g in f.get("goals") or []]
    out = "Finanzprofil: " + ("; ".join(parts) if parts else "noch leer")
    out += "\nZiele: " + ("; ".join(goals) if goals else "noch keine")
    if f.get("notes"):
        out += f"\nNotizen: {f['notes']}"
    return out


def snapshot(con, profile: dict) -> str:
    s = profile["settings"]
    cur = s["currency"]
    pf = service.portfolio(con, profile)
    lines = [f"Profil {profile['name']}, Währung {cur}, Steuer-Modus {s['tax']}, Inflation {s['inflation_geo']}."]
    if pf["empty"]:
        lines.append("Bestand: noch nichts eingetragen.")
    else:
        alloc = ", ".join(f"{a['name']} {fmt_money(a['value'], cur)} ({fmt_num(a['share'] * 100, 0)} %)" for a in pf["alloc"])
        lines.append(f"Bestand: {fmt_money(pf['total'], cur)} – {alloc}.")
        if pf["gain"] is not None:
            lines.append(f"Gewinn seit Kauf (wo bekannt): {fmt_money(pf['gain'], cur)}.")
        for p in pf["plans"]:
            lines.append(f"Sparplan {ASSETS.get(p['asset'], {}).get('name', p['asset'])}: {fmt_money(p['amount'], p['cur'])}"
                         f"/Monat seit {p['start']}{'' if p['running'] else ' (beendet)'}.")
    rows = []
    for a in [x for x in s["assets"] if x in ASSETS][:8]:
        try:
            p = service.price_display(con, profile, a)
            c7 = p["change"].get("7d")
            rows.append(f"{p['label']} {p['text']}" + (f" (7 T. {'+' if c7 >= 0 else '−'}{fmt_num(abs(c7) * 100, 1)} %)"
                                                         if c7 is not None else ""))
        except Exception:
            continue
    lines.append("Beobachtet: " + "; ".join(rows))
    lines.append(finance_text(profile))
    return "\n".join(lines)


def run_tool(con, profile: dict, name: str, args: dict) -> tuple[dict, dict | None]:
    """Führt ein Werkzeug aus. Rückgabe: (Ergebnis für das Modell, Ereignis für die Website oder None)."""
    pid = profile["id"]
    if name == "get_overview":
        o = service.overview(con, profile)
        pf = service.portfolio(con, profile)
        return {"snapshot": snapshot(con, profile), "headline": o["headline"],
                "positions": [{"title": p["title"], "value": round(p["value"], 2), "gain": p["gain"]} for p in pf["positions"]],
                "next_steps": [n["title"] + ": " + n["text"] for n in o["next"]],
                "goal": o["goal"]["text"] if o["goal"] else None}, None
    if name == "get_market":
        a = str(args.get("asset") or "").upper()
        if a not in ASSETS:
            hit = markets.by_symbol(a) or next((k for k, m in ASSETS.items() if m["name"].upper() == a), None)
            if not hit:
                return {"error": f"Unbekannter Markt {a}. Nutze search_markets."}, None
            a = hit
        row = service.market_row(con, profile, a)
        return {"market": markets.public(a), "price": row["text"], "unit": row["label"], "change": row["change"],
                "trend": row["trend"], "volatility": row["calm"], "story": row["story"],
                "years": service.yearly_returns(con, a)[-10:], "drawdowns": service.drawdowns(con, a, top=3)}, None
    if name == "search_markets":
        return {"results": [{k: r.get(k) for k in ("id", "symbol", "name", "kind_label", "known")}
                            for r in markets.search(str(args.get("query") or ""), 8)]}, None
    if name == "list_calculators":
        return {"calculators": [{"id": c["id"], "title": c["title"], "question": c["question"],
                                 "fields": [{k: f.get(k) for k in ("key", "label", "type", "default", "options")}
                                            for f in c["fields"]]} for c in calcs.catalog(profile)]}, None
    if name == "run_calculator":
        cid = str(args.get("id") or "")
        inputs = args.get("inputs") or {}
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except json.JSONDecodeError:
                inputs = {}
        try:
            r = calcs.run(con, profile, cid, inputs)
        except Exception as e:
            return {"error": str(e)}, None
        out = {k: r.get(k) for k in ("text", "tiles", "notes", "net_label") if r.get(k)}
        if "end" in r:
            out["end_percentiles"] = r["end"]
        for k in ("monthly_50", "monthly_75", "monthly_90", "final", "invested", "p_loss"):
            if k in r:
                out[k] = r[k]
        return out, {"type": "calc", "id": cid, "inputs": inputs, "title": r.get("title")}
    if name == "update_finance_profile":
        fields = args.get("fields") or {}
        if isinstance(fields, str):
            try:
                fields = json.loads(fields)
            except json.JSONDecodeError:
                fields = {}
        s = profiles.update_settings(con, pid, {"finance": fields})
        p2 = profiles.get(con, pid)
        profile["settings"] = p2["settings"]
        return {"saved": True, "finance": s["finance"], "personal_rate": s["personal_rate"]}, \
            {"type": "saved", "fields": list(fields)}
    if name == "watch_market":
        a = str(args.get("asset") or "").upper()
        if a not in ASSETS:
            return {"error": f"Unbekannter Markt {a}."}, None
        cur = list(profile["settings"]["assets"])
        if args.get("watch"):
            cur = cur + [a] if a not in cur else cur
        else:
            cur = [x for x in cur if x != a]
        s = profiles.update_settings(con, pid, {"assets": cur})
        profile["settings"] = s
        return {"watchlist": s["assets"]}, {"type": "saved", "fields": ["assets"]}
    return {"error": f"Unbekanntes Werkzeug {name}"}, None


# ---------- Gespräch ----------
SYSTEM = """Du bist der Finanz-Ratgeber im „Invest-Ratgeber“, einer privaten App, mit der {name} und Freunde ihr Geld \
verstehen und planen. Du sprichst Deutsch und duzt. Antworte kurz (meist 3–8 Sätze oder eine kurze Liste), konkret \
und mit Zahlen. Du schreibst für Erwachsene um die 40, die noch nie etwas mit Geldanlage zu tun hatten. Sie sollen \
alles beim ersten Lesen verstehen. Schreib ruhig und klar wie ein guter Freund, nicht kindlich, nicht belehrend.
- Erst die Antwort in ein bis zwei Sätzen („Was heißt das für mich?“), danach die Details.
- Kurze Sätze (höchstens etwa 15 Wörter), ein Gedanke pro Satz, aktiv statt Passiv.
- Erklär jedes Fachwort beim ersten Mal im Gespräch in einem Halbsatz oder in Klammern. Abkürzungen nie unerklärt.
- Gib Zahlen eine Bedeutung, mit einem Rechenbeispiel in runden Beträgen (100 €, 1.000 €, 10.000 €): \
„7 % pro Jahr, aus 1.000 € werden also nach einem Jahr etwa 1.070 €.“
- Deutsche Wörter: Anlage (nicht Asset), Mischung (nicht Portfolio), Umschichten (nicht Rebalancing), Absturz vom Hoch \
(nicht Drawdown), Schwankung (nicht Volatilität), Wertzuwachs (nicht Performance), Streuen (nicht Diversifikation).
- Ehrlich bei Unsicherheit: „kann“, „in der Vergangenheit“, „in 9 von 10 Fällen“. Nie etwas versprechen.

Immer dieselben Wörter mit diesen Erklärungen: Kurs = aktueller Preis einer Anlage. Aktie = kleiner Anteil an einer \
Firma. Index = Liste von Firmen, deren Kurse zusammen gemessen werden (DAX = 40 große deutsche Firmen). ETF = Korb mit \
vielen Aktien auf einmal, den man an der Börse kauft; er bildet einen Index nach. MSCI World = Index mit rund 1.400 \
großen Firmen aus 23 Industrieländern. Depot = Konto für Aktien und ETFs bei Bank oder Online-Broker. Sparplan = jeden \
Monat wird automatisch ein fester Betrag angelegt. Rendite nur mit Erklärung (Wertzuwachs pro Jahr im Schnitt). Zinseszins = \
Gewinne bringen selbst wieder Gewinne. Inflation = Preise steigen; bei 2 % im Jahr kaufen 100 € in 10 Jahren nur noch so \
viel wie heute 82 €. Tagesgeld = Sparkonto mit Zinsen, jederzeit verfügbar, bis 100.000 € pro Bank gesetzlich \
abgesichert. Laufende Kosten = was ein ETF jedes Jahr abzieht, meist 0,1–0,3 % (nicht TER). Abgeltungsteuer = rund 26 % \
auf Gewinne aus Geldanlagen. Freibetrag = 1.000 € Gewinn pro Jahr steuerfrei (Paare 2.000 €), dafür bei der Bank einen \
Freistellungsauftrag stellen. Spanne = Bereich, in dem das Ergebnis in 9 von 10 Fällen landet (nicht Perzentil). \
Mittlerer Verlauf = die Hälfte liegt darüber, die Hälfte darunter (nicht Median). Prognose = immer eine Spanne, nie eine \
Vorhersage. Bitcoin/Krypto = digitales Geld ohne Bank dahinter, mit sehr starker Schwankung.

Werkzeuge: Hol echte Daten mit get_overview, get_market, search_markets; rechne mit run_calculator (Liste: \
list_calculators) statt im Kopf. Erfinde nie Preise, Renditen oder Bestände.

So berätst du (in dieser Reihenfolge):
1. Ziel und Zeitraum klären: Wofür wird gespart, wann wird das Geld gebraucht? Unter 3 Jahren: Tagesgeld/Festgeld, \
nichts Schwankendes. 3–10 Jahre: Mischung mit Anleihen (Kredite an Staaten/Firmen). Über 10 Jahre: breit \
gestreute Aktien-ETFs als Kern.
2. Teure Schulden zuerst tilgen. Notgroschen (für Notfälle): 3 Monatsausgaben (Selbstständige 6) auf Tagesgeld.
3. Sparrate aus Einkommen und Ausgaben ableiten (Richtwert 10–20 % vom Netto, Rechner „budget“).
4. Risiko passend zur Person: Frag, wie sie reagieren würde, wenn ihr Depot um 30 % fällt, aus 10.000 € also 7.000 € \
werden. Krypto, Einzelaktien und Edelmetalle höchstens als kleine Beimischung (etwa 5–10 %), nur mit Geld, dessen \
Verlust verkraftbar wäre.
5. Kosten und Steuern: günstige ETFs (laufende Kosten unter 0,3 %), Freibetrag (Sparerpauschbetrag) 1.000 €/2.000 €, \
bei Aktien-ETFs sind 30 % der Gewinne steuerfrei (Teilfreistellung), Gewinne mit Gold/Krypto nach 1 Jahr steuerfrei. \
Steuersatz mit dem Rechner „taxrate“.
6. Zeigen statt behaupten: mit run_calculator vorrechnen („future“, „goal“, „retirement“, „compound“ …) und Spannen \
nennen (guter, mittlerer, schlechter Fall).

Gesprächsführung:
- Stell höchstens ein bis zwei Fragen auf einmal. Fehlen Angaben, frag nach statt zu raten.
- Erfährst du etwas über die Person (Einkommen, Ausgaben, Erspartes, Schulden, Ziele, Risiko, Alter, Steuern), \
speichere es sofort mit update_finance_profile und sag kurz „Hab ich mir gemerkt“.
- Verlinke passende Seiten mit Markdown-Links: [Mein Bestand](#/bestand), [Märkte](#/maerkte), [Ziele](#/ziele), \
[Einstellungen](#/einstellungen), Rechner als [Titel](#/rechner/<id>), Märkte als [Name](#/maerkte/<ID>).
- Keine Kursprognosen („wird steigen“) und keine Kaufempfehlung für einzelne Aktien oder Coins. Du bist kein \
zugelassener Anlageberater: Bei großen Summen, Immobilien, Versicherungen oder schwierigen Steuerfragen empfiehlst \
du eine unabhängige Honorar- oder Steuerberatung.

Aktueller Stand (Kurzfassung, Details per Werkzeug):
{snapshot}

Die Person sieht gerade: {page}
Heute ist {date}."""


def history(con, pid: int, limit: int = 100) -> list[dict]:
    rows = con.execute("SELECT id, ts, role, content FROM ai_messages WHERE profile_id=? ORDER BY id DESC LIMIT ?",
                       (pid, limit)).fetchall()
    return [{"id": r[0], "ts": r[1], "role": r[2], "content": r[3]} for r in reversed(rows)]


def clear_history(con, pid: int) -> None:
    con.execute("DELETE FROM ai_messages WHERE profile_id=?", (pid,))
    con.commit()


def _save(con, pid: int, role: str, content: str) -> None:
    con.execute("INSERT INTO ai_messages(profile_id, ts, role, content) VALUES (?,?,?,?)",
                (pid, int(time.time()), role, content))
    con.execute("DELETE FROM ai_messages WHERE profile_id=? AND id NOT IN "
                "(SELECT id FROM ai_messages WHERE profile_id=? ORDER BY id DESC LIMIT 200)", (pid, pid))
    con.commit()


def chat(con, profile: dict, message: str, page: str = "") -> Iterator[dict]:
    """Ein Gesprächsschritt mit Werkzeug-Schleife. Liefert Ereignisse für die Website."""
    c = effective(con, profile)
    if not c["ready"]:
        yield {"type": "error", "text": c["reason"]}
        return
    message = message.strip()[:4000]
    if not message:
        yield {"type": "error", "text": "Deine Nachricht ist leer. Schreib bitte eine Frage."}
        return
    past = [m for m in history(con, profile["id"], HISTORY) if m["role"] in ("user", "assistant")]
    _save(con, profile["id"], "user", message)
    system = SYSTEM.format(name=profile["name"], snapshot=snapshot(con, profile), page=(page or "Überblick")[:1500],
                           date=dt.date.today().strftime("%d.%m.%Y"))
    msgs = [{"role": m["role"], "content": m["content"]} for m in past] + [{"role": "user", "content": message}]
    extra: list = []  # Responses: Werkzeug-Aufrufe und -Ergebnisse
    tools = TOOLS
    text_all = ""
    for rnd in range(MAX_ROUNDS + 1):
        calls, raws, text = [], [], ""
        try:
            for kind, val in _stream_round(c, system, msgs, tools if rnd < MAX_ROUNDS else None, extra_input=extra):
                if kind == "text":
                    text += val
                    yield {"type": "delta", "text": val}
                elif kind == "call":
                    calls.append(val)
                elif kind == "raw":
                    raws.append(val)
        except AIError as e:
            if tools and rnd == 0 and "tool" in str(e).lower():
                tools = None  # Server kann keine Werkzeuge → ohne weiter
                yield {"type": "status", "text": "Dieser KI-Server kann nicht selbst nachschauen oder rechnen. "
                                                 "Ich antworte deshalb nur mit den Daten, die ich schon habe."}
                continue
            log.warning("KI-Fehler: %s", e)
            yield {"type": "error", "text": f"Der KI-Server hat einen Fehler gemeldet. Versuch es bitte gleich noch "
                                            f"einmal. Technische Details: {e}"}
            return
        except requests.RequestException as e:
            yield {"type": "error", "text": f"Der KI-Server ist gerade nicht erreichbar ({type(e).__name__}). Versuch "
                                            "es in ein paar Minuten noch einmal. Klappt es dann nicht, sag dem Admin Bescheid."}
            return
        text_all += text
        if not calls:
            break
        if c["api"] == "responses":
            if text:
                extra.append({"role": "assistant", "content": text})
            extra += raws
        else:
            msgs.append({"role": "assistant", "content": text or None,
                         "tool_calls": [{"id": k["id"], "type": "function",
                                         "function": {"name": k["name"], "arguments": k["arguments"]}} for k in calls]})
        for k in calls:
            yield {"type": "status", "text": STATUS.get(k["name"], "Einen Moment …")}
            try:
                args = json.loads(k["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result, event = run_tool(con, profile, k["name"], args if isinstance(args, dict) else {})
            except Exception as e:
                log.exception("Werkzeug %s", k["name"])
                result, event = {"error": f"{type(e).__name__}: {e}"}, None
            if event:
                yield event
            out = _compact(result)
            if c["api"] == "responses":
                extra.append({"type": "function_call_output", "call_id": k["id"], "output": out})
            else:
                msgs.append({"role": "tool", "tool_call_id": k["id"], "content": out})
        if text:
            yield {"type": "delta", "text": "\n\n"}
            text_all += "\n\n"
    if text_all.strip():
        _save(con, profile["id"], "assistant", text_all.strip())
    yield {"type": "done"}
