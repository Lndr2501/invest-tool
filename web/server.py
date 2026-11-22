"""Web-Oberfläche v3: FastAPI-Backend + statische Seite (Alpine.js, ECharts). Nur im Heimnetz.

Start: .venv/bin/python -m invest web [host] [port]
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from invest import advice, analysis, assistant, calcs, collect, db, markets, profiles, service, taxde, telegram
from invest.config import ASSETS
from invest.units import (COIN_SIZES, COINS, CURRENCIES, FINENESS, INFLATION_GEOS, OZ, UNIT_LABEL)

STATIC = Path(__file__).parent / "static"
COOKIE = "invest_sess"
_pin_fail: dict[int, list[float]] = {}


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


class J(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(_clean(content), ensure_ascii=False, separators=(",", ":")).encode()


app = FastAPI(title="Invest-Tool", default_response_class=J, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    # Schutz gegen Fremdseiten: schreibende Anfragen brauchen den eigenen Header (erzwingt CORS-Vorabprüfung)
    if request.method in ("POST", "PUT", "DELETE") and request.headers.get("x-invest") != "1":
        return J({"detail": "Diese Anfrage wurde abgelehnt. Bitte lade die Seite neu."}, status_code=403)
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


def con_dep():
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def me(request: Request, con=Depends(con_dep)) -> dict:
    pid = profiles.read_session(request.cookies.get(COOKIE))
    p = profiles.get(con, pid) if pid else None
    if not p:
        raise HTTPException(401, "Bitte wähle zuerst dein Profil.")
    return p


def _login(resp: Response, pid: int) -> None:
    resp.set_cookie(COOKIE, profiles.make_session(pid), max_age=180 * 86400, httponly=True, samesite="strict")


def _err(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


def _public(p: dict, con=None) -> dict:
    out = {"id": p["id"], "name": p["name"], "has_pin": p["has_pin"], "settings": p["settings"],
           "telegram": bool(p["tg_chat_id"])}
    if con is not None:
        out["is_admin"] = profiles.admin_id(con) == p["id"]
    return out


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        raise HTTPException(400, "Die Eingaben kamen nicht richtig an. Bitte lade die Seite neu.") from None
    if not isinstance(b, dict):
        raise HTTPException(400, "Die Eingaben kamen nicht richtig an. Bitte lade die Seite neu.")
    return b


# ---------- Stammdaten ----------
@app.get("/api/meta")
def meta(con=Depends(con_dep)):
    return {
        "assets": {a: markets.public(a) for a in ASSETS},
        "kinds": [{"id": k, "label": markets.KINDS[k]["label"]} for k in markets.KIND_ORDER],
        "metals": markets.METALS, "core": markets.CORE,
        "goal_purposes": profiles.GOAL_PURPOSES,
        "calc_categories": [{"id": k, "label": v} for k, v in calcs.CATEGORIES],
        "currencies": {k: {"symbol": v[0], "name": v[1]} for k, v in CURRENCIES.items()},
        "fineness": {k: [{"value": f, "label": lbl} for f, lbl in v] for k, v in FINENESS.items()},
        "coins": COINS, "coin_sizes": [{"value": v, "label": lbl} for v, lbl in COIN_SIZES],
        "units": UNIT_LABEL, "oz": OZ, "geos": INFLATION_GEOS,
        "report_content": profiles.REPORT_CONTENT, "alert_rules": profiles.ALERT_RULES,
        "costs": {a: markets.costs_for(a) for a in ASSETS}, "bot": telegram.bot_username(con),
    }


# ---------- Profile ----------
@app.get("/api/profiles")
def list_profiles(con=Depends(con_dep)):
    profiles.migrate_v1(con)
    return [{"id": p["id"], "name": p["name"], "has_pin": p["has_pin"]} for p in profiles.list_profiles(con)]


@app.post("/api/profiles")
async def create_profile(request: Request, response: Response, con=Depends(con_dep)):
    body = await request.json()
    try:
        p = profiles.create(con, body.get("name", ""), body.get("pin") or None, body.get("settings") or {})
    except profiles.ProfileError as e:
        raise _err(e) from None
    _login(response, p["id"])
    return _public(p, con)


@app.post("/api/login")
async def login(request: Request, response: Response, con=Depends(con_dep)):
    body = await request.json()
    pid = int(body.get("id") or 0)
    p = profiles.get(con, pid)
    if not p:
        raise HTTPException(404, "Dieses Profil gibt es nicht mehr. Bitte lade die Seite neu.")
    if p["has_pin"]:
        fails = [t for t in _pin_fail.get(pid, []) if time.time() - t < 300]
        if len(fails) >= 5:
            raise HTTPException(429, "Die PIN war zu oft falsch. Bitte warte 5 Minuten und versuch es dann noch einmal.")
        if not profiles.check_pin(str(body.get("pin") or ""), profiles.pin_hash(con, pid)):
            _pin_fail[pid] = fails + [time.time()]
            raise HTTPException(403, "Die PIN stimmt nicht. Bitte versuch es noch einmal.")
        _pin_fail.pop(pid, None)
    _login(response, pid)
    return _public(p, con)


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/session")
def get_session(request: Request, con=Depends(con_dep)):
    """Wie /api/me, aber ohne 401 – für den ersten Seitenaufruf."""
    pid = profiles.read_session(request.cookies.get(COOKIE, ""))
    p = profiles.get(con, pid) if pid else None
    return {"profile": _public(p, con) if p else None}


@app.get("/api/me")
def get_me(p=Depends(me), con=Depends(con_dep)):
    return _public(p, con)


@app.put("/api/me")
async def put_me(request: Request, p=Depends(me), con=Depends(con_dep)):
    body = await request.json()
    try:
        if "name" in body and body["name"] != p["name"]:
            profiles.rename(con, p["id"], body["name"])
        if "settings" in body:
            st = dict(body["settings"] or {})
            st.pop("ai", None)  # KI-Zugang nur über /api/ai/own (Key-Schutz bei Adresswechsel)
            profiles.update_settings(con, p["id"], st)
        if "pin" in body:
            if p["has_pin"] and not profiles.check_pin(str(body.get("old_pin") or ""), profiles.pin_hash(con, p["id"])):
                raise profiles.ProfileError("Deine bisherige PIN stimmt nicht. Bitte gib sie noch einmal ein.")
            profiles.set_pin(con, p["id"], body["pin"] or None)
    except profiles.ProfileError as e:
        raise _err(e) from None
    return _public(profiles.get(con, p["id"]), con)


@app.delete("/api/me")
async def delete_me(request: Request, response: Response, p=Depends(me), con=Depends(con_dep)):
    body = await request.json()
    if (body.get("confirm") or "").strip().lower() != p["name"].lower():
        raise HTTPException(400, "Zum Löschen bitte den Namen deines Profils genau eintippen. Das schützt vor "
                                 "versehentlichem Löschen.")
    if p["has_pin"] and not profiles.check_pin(str(body.get("pin") or ""), profiles.pin_hash(con, p["id"])):
        raise HTTPException(403, "Die PIN stimmt nicht. Bitte versuch es noch einmal.")
    profiles.delete(con, p["id"])
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ---------- Bestand ----------
@app.get("/api/portfolio")
def get_portfolio(p=Depends(me), con=Depends(con_dep)):
    return service.portfolio(con, p)


@app.get("/api/portfolio/history")
def get_history(p=Depends(me), con=Depends(con_dep)):
    return service.portfolio_history(con, p)


@app.post("/api/holdings")
async def add_holding(request: Request, p=Depends(me), con=Depends(con_dep)):
    try:
        return profiles.save_holding(con, p["id"], await request.json())
    except (profiles.ProfileError, ValueError, TypeError) as e:
        raise _err(e) from None


@app.put("/api/holdings/{hid}")
async def edit_holding(hid: int, request: Request, p=Depends(me), con=Depends(con_dep)):
    try:
        return profiles.save_holding(con, p["id"], await request.json(), hid)
    except (profiles.ProfileError, ValueError, TypeError) as e:
        raise _err(e) from None


@app.delete("/api/holdings/{hid}")
def del_holding(hid: int, p=Depends(me), con=Depends(con_dep)):
    profiles.delete_holding(con, p["id"], hid)
    return {"ok": True}


@app.post("/api/plans")
async def add_plan(request: Request, p=Depends(me), con=Depends(con_dep)):
    try:
        return profiles.save_plan(con, p["id"], await request.json())
    except (profiles.ProfileError, ValueError, TypeError) as e:
        raise _err(e) from None


@app.put("/api/plans/{plan_id}")
async def edit_plan(plan_id: int, request: Request, p=Depends(me), con=Depends(con_dep)):
    try:
        return profiles.save_plan(con, p["id"], await request.json(), plan_id)
    except (profiles.ProfileError, ValueError, TypeError) as e:
        raise _err(e) from None


@app.delete("/api/plans/{plan_id}")
def del_plan(plan_id: int, p=Depends(me), con=Depends(con_dep)):
    profiles.delete_plan(con, p["id"], plan_id)
    return {"ok": True}


@app.post("/api/price-at")
async def price_at(request: Request, p=Depends(me), con=Depends(con_dep)):
    """Hilfe beim Eintragen: Was war die Position am Kaufdatum wert? (für „Kaufpreis unbekannt“)"""
    body = await request.json()
    try:
        h = profiles.normalize_holding(body)
    except (profiles.ProfileError, ValueError, TypeError) as e:
        raise _err(e) from None
    cur = p["settings"]["currency"]
    import pandas as pd
    day = pd.Timestamp(h["buy_date"]) if h["buy_date"] else pd.Timestamp.today()
    price = service._price_at(con, h["asset"], day)
    if price != price:
        raise HTTPException(400, "Für dieses Datum hat die App noch keine Kurse. Wähle ein späteres Datum oder trag "
                                 "den Kaufpreis selbst ein.")
    return {"value": h["quantity"] * price * service.fx_at(con, cur, day), "currency": cur,
            "fine_g": h["quantity"] * OZ if markets.is_metal(h["asset"]) else None}


# ---------- Ratgeber, Märkte, Rechner ----------
@app.get("/api/overview")
def get_overview(p=Depends(me), con=Depends(con_dep)):
    return service.overview(con, p)


@app.get("/api/market/{asset}")
def get_market(asset: str, range: str = "1y", p=Depends(me), con=Depends(con_dep)):
    if asset not in ASSETS or range not in ("1d", "1w", "1m", "1y", "5y", "10y", "max"):
        raise HTTPException(404, "Diesen Markt oder Zeitraum kennt die App nicht.")
    out = service.market(con, p, asset, range)
    ana = db.kv_get(con, f"analysis:{asset}") or {}
    pdp = service.price_display(con, p, asset)
    out.update(price=pdp, story=service.asset_story(con, asset, ana, pdp["change"]),
               years=service.yearly_returns(con, asset), drawdowns=service.drawdowns(con, asset),
               trend=(ana.get("trend") or {}).get("label"), vol=(ana.get("vol") or {}).get("label"),
               regime=(ana.get("regime") or {}).get("current"), since=ana.get("history_since"))
    cross = db.kv_get(con, "analysis:_cross") or {}
    corr = (cross.get("1y") or {}).get(asset) or {}
    out["corr"] = {a: v for a, v in corr.items() if a != asset}
    return out


def _calc(fn, con, p, body):
    try:
        return fn(con, p, body)
    except ValueError as e:
        raise _err(e) from None


@app.post("/api/calc/future")
async def calc_future(request: Request, p=Depends(me), con=Depends(con_dep)):
    return _calc(service.calc_future, con, p, await request.json())


@app.post("/api/calc/goal")
async def calc_goal(request: Request, p=Depends(me), con=Depends(con_dep)):
    return _calc(service.calc_goal, con, p, await request.json())


@app.post("/api/calc/past")
async def calc_past(request: Request, p=Depends(me), con=Depends(con_dep)):
    return _calc(service.calc_past, con, p, await request.json())


@app.get("/api/methodik")
def get_methodik(p=Depends(me), con=Depends(con_dep)):
    held = [h["asset"] for h in profiles.list_holdings(con, p["id"])]
    return service.methodik(con, list(dict.fromkeys(p["settings"]["assets"] + held)))


# ---------- Märkte (Katalog, Suche, eigene) ----------
@app.get("/api/markets")
def get_markets(p=Depends(me), con=Depends(con_dep)):
    return {"markets": service.market_list(con, p), "watch": p["settings"]["assets"],
            "kinds": [{"id": k, "label": markets.KINDS[k]["label"]} for k in markets.KIND_ORDER]}


@app.get("/api/markets/search")
def search_markets(q: str = "", p=Depends(me)):
    return {"results": markets.search(q[:60], 12)}


@app.post("/api/markets")
async def add_market(request: Request, p=Depends(me), con=Depends(con_dep)):
    body = await _body(request)
    try:
        m = markets.add_custom(con, str(body.get("symbol") or ""), p["id"])
    except ValueError as e:
        raise _err(e) from None
    except Exception as e:
        raise HTTPException(502, f"Die Kurse ließen sich gerade nicht laden ({type(e).__name__}). Bitte versuch es "
                                 "später noch einmal.") from None
    a = m["id"]
    if db.kv_get(con, f"analysis:{a}") is None:
        try:
            collect.init_market(con, a)
            db.kv_set(con, f"analysis:{a}", analysis.analyze_asset(con, a, time.time()))
            con.commit()
        except Exception as e:  # Markt bleibt angelegt; der nächste Lauf holt die Daten nach
            raise HTTPException(502, f"Der Markt ist angelegt, aber die Kurse fehlen noch. Die App lädt sie beim "
                                     f"nächsten Abruf automatisch nach, meist in wenigen Minuten. ({e})") from None
        service.clear_cache()
    if body.get("watch", True) and a not in p["settings"]["assets"]:
        profiles.update_settings(con, p["id"], {"assets": p["settings"]["assets"] + [a]})
    return {"market": markets.public(a)}


@app.put("/api/watch/{asset}")
async def watch(asset: str, request: Request, p=Depends(me), con=Depends(con_dep)):
    if asset not in ASSETS:
        raise HTTPException(404, "Diesen Markt kennt die App nicht.")
    on = bool((await _body(request)).get("watch", True))
    cur = [a for a in p["settings"]["assets"] if a != asset] + ([asset] if on else [])
    if not cur:
        raise HTTPException(400, "Du musst mindestens einen Markt beobachten. Füge erst einen anderen hinzu, dann "
                                 "kannst du diesen entfernen.")
    s = profiles.update_settings(con, p["id"], {"assets": cur})
    return {"watch": s["assets"]}


# ---------- Rechner-Baukasten ----------
@app.get("/api/calcs")
def get_calcs(p=Depends(me)):
    return {"categories": [{"id": k, "label": v} for k, v in calcs.CATEGORIES], "calcs": calcs.catalog(p)}


@app.post("/api/calc/{cid}")
async def run_calc(cid: str, request: Request, p=Depends(me), con=Depends(con_dep)):
    body = await _body(request)
    try:
        return calcs.run(con, p, cid, body)
    except (calcs.CalcError, ValueError) as e:
        raise _err(e) from None


@app.get("/api/tax")
def tax_rates(zve: float, married: bool = False, church: str = "none", p=Depends(me)):
    return taxde.rates(max(min(zve, 1e8), 0), married, taxde.church_rate(church))


@app.post("/api/start")
async def start_suggest(request: Request, p=Depends(me), con=Depends(con_dep)):
    """Start-Assistent: Antworten → erster Plan (Reihenfolge, Sparrate, Mischung, Beispielrechnung)."""
    try:
        return advice.suggest(con, p, await _body(request))
    except (TypeError, ValueError) as e:
        raise _err(e) from None


@app.get("/api/finance")
def get_finance(p=Depends(me), con=Depends(con_dep)):
    """Finanzprofil mit abgeleiteten Zahlen (frei verfügbar, Notgroschen, Sparbedarf je Ziel)."""
    s = p["settings"]
    f = s["finance"]
    pf = service.portfolio(con, p)
    goals = []
    for g in f["goals"]:
        plan = service.goal_plan(g, pf["total"] if g["purpose"] in ("wealth", "retirement") else 0.0)
        goals.append({**g, "plan": plan})
    free = (f["income"] - f["expenses"]) if f.get("income") and f.get("expenses") is not None else None
    return {"finance": f, "goals": goals, "free": free,
            "emergency": 3 * f["expenses"] if f.get("expenses") else None,
            "personal_rate": s["personal_rate"], "personal_rate_auto": s["personal_rate_auto"],
            "allowance": s["allowance"], "portfolio_total": pf["total"],
            "planned": sum(x["amount"] for x in pf["plans"] if x["running"])}


# ---------- KI-Assistent ----------
@app.get("/api/ai")
def ai_conf(p=Depends(me), con=Depends(con_dep)):
    return assistant.public_conf(con, p)


@app.put("/api/ai/own")
async def ai_own(request: Request, p=Depends(me), con=Depends(con_dep)):
    try:
        assistant.set_own(con, p, await _body(request))
    except (assistant.AIError, profiles.ProfileError) as e:
        raise _err(e) from None
    return assistant.public_conf(con, profiles.get(con, p["id"]))


@app.put("/api/ai/global")
async def ai_global(request: Request, p=Depends(me), con=Depends(con_dep)):
    if profiles.admin_id(con) != p["id"]:
        raise HTTPException(403, "Den gemeinsamen KI-Zugang darf nur das Admin-Profil ändern. Das ist meist das "
                                 "Profil, das zuerst angelegt wurde.")
    try:
        assistant.set_global(con, await _body(request))
    except assistant.AIError as e:
        raise _err(e) from None
    return assistant.public_conf(con, p)


@app.post("/api/ai/test")
async def ai_test(request: Request, p=Depends(me), con=Depends(con_dep)):
    """Zugang prüfen (scope global|own). Für global nur Admin."""
    scope = (await _body(request)).get("scope", "own")
    if scope == "global":
        if profiles.admin_id(con) != p["id"]:
            raise HTTPException(403, "Das darf nur das Admin-Profil.")
        c = {**assistant.global_conf(con), "key": profiles.secret_get(con, "global", "ai_key")}
    else:
        own = p["settings"].get("ai") or {}
        c = {"base_url": own.get("base_url", ""), "model": own.get("model", ""), "api": own.get("api", "chat"),
             "effort": own.get("effort", ""), "max_tokens": own.get("max_tokens", 8000),
             "key": profiles.secret_get(con, f"profile:{p['id']}", "ai_key")}
    if not c["base_url"]:
        raise HTTPException(400, "Bitte trag zuerst die Adresse des KI-Servers ein.")
    if scope != "global" and not assistant.is_local(c["base_url"]) and not (p["settings"].get("ai") or {}).get("consent"):
        raise HTTPException(400, "Der KI-Server steht nicht in deinem Heimnetz. Bitte stimm erst zu, dass deine "
                                 "Daten dorthin geschickt werden dürfen.")
    c.setdefault("model", "")
    return assistant.test(c) if c["model"] else {"ok": False, "models": assistant.list_models(c), "error": "Bitte wähle ein Modell aus der Liste."}


@app.get("/api/ai/history")
def ai_history(p=Depends(me), con=Depends(con_dep)):
    e = assistant.effective(con, p)
    return {"messages": assistant.history(con, p["id"], 60), "ready": e["ready"], "reason": e["reason"],
            "model": e["model"], "local": e["local"]}


@app.delete("/api/ai/history")
def ai_clear(p=Depends(me), con=Depends(con_dep)):
    assistant.clear_history(con, p["id"])
    return {"ok": True}


@app.post("/api/ai/chat")
async def ai_chat(request: Request, p=Depends(me)):
    body = await _body(request)
    msg, page = str(body.get("message") or ""), str(body.get("page") or "")

    def gen():
        con = db.connect()  # eigene Verbindung: lebt so lange wie der Stream
        try:
            prof = profiles.get(con, p["id"])
            for ev in assistant.chat(con, prof, msg, page):
                yield "data: " + json.dumps(_clean(ev), ensure_ascii=False) + "\n\n"
        except Exception as e:  # noqa: BLE001 – Fehler als Ereignis an die Seite
            yield "data: " + json.dumps({"type": "error", "text": f"Da ist etwas schiefgelaufen ({type(e).__name__}). "
                                                          "Bitte versuch es noch einmal."}) + "\n\n"
        finally:
            con.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------- Telegram ----------
@app.post("/api/telegram/link")
def tg_link(p=Depends(me), con=Depends(con_dep)):
    bot = telegram.bot_username(con)
    if not bot:
        raise HTTPException(400, "Der Telegram-Bot ist noch nicht eingerichtet. Das muss der Admin erst erledigen.")
    code = profiles.new_link_code(con, p["id"])
    return {"url": f"https://t.me/{bot}?start={code}", "bot": bot, "code": code}


@app.get("/api/telegram/status")
def tg_status(p=Depends(me), con=Depends(con_dep)):
    rows = con.execute("SELECT ts, kind, text, sent FROM tg_log WHERE profile_id=? ORDER BY ts DESC LIMIT 15",
                       (p["id"],)).fetchall()
    return {"linked": bool(p["tg_chat_id"]), "schedule": telegram.schedule_text(p["settings"]),
            "log": [{"ts": r[0], "kind": r[1], "text": r[2], "sent": bool(r[3])} for r in rows]}


@app.get("/api/telegram/preview")
def tg_preview(p=Depends(me), con=Depends(con_dep)):
    return {"text": telegram.report_text(con, p)}


@app.post("/api/telegram/test")
def tg_test(p=Depends(me), con=Depends(con_dep)):
    if not p["tg_chat_id"]:
        raise HTTPException(400, "Telegram ist noch nicht verbunden. Tippe zuerst auf „Telegram verbinden“.")
    text = telegram.report_text(con, p)
    ok = telegram.send(p["tg_chat_id"], text)
    telegram.log_send(con, p["id"], "report_manual", "web", text, ok)
    if not ok:
        raise HTTPException(502, "Die Nachricht ließ sich nicht senden. Bitte versuch es später noch einmal.")
    return {"ok": True}


@app.delete("/api/telegram")
def tg_unlink(p=Depends(me), con=Depends(con_dep)):
    if p["tg_chat_id"]:
        telegram.send(p["tg_chat_id"], "👋 Dieser Chat ist jetzt von deinem Profil getrennt. Du bekommst keine "
                                         "Nachrichten mehr. Neu verbinden kannst du jederzeit auf der Website.")
    profiles.set_chat(con, p["id"], None)
    return {"ok": True}


@app.get("/api/status")
def status(con=Depends(con_dep)):
    row = con.execute("SELECT MAX(ts) FROM runs WHERE job='collect' AND ok=1").fetchone()
    return {"collect": row[0], "now": int(time.time())}


# ---------- Seite ----------
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
