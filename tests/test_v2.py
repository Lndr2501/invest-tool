"""Tests für v2: Einheiten, Profile, Bestand, Rechner, Telegram-Verteilung und Web-API.

Läuft auf einer Kopie der echten Datenbank (Kurse werden gebraucht), nie auf dem Original.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from invest import alerts, config, db, profiles, service, telegram, units


@pytest.fixture(scope="module")
def dbcopy(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.sqlite"
    src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(path)
    src.backup(dst)
    src.close()
    dst.close()
    old = db.DB_PATH
    db.DB_PATH = path
    con = db.connect()
    for t in ("holdings", "plans", "tg_links", "tg_log", "profiles"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
    service.clear_cache()
    yield path
    db.DB_PATH = old
    service.clear_cache()


@pytest.fixture()
def con(dbcopy):
    c = db.connect()
    yield c
    c.close()


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(telegram, "send", lambda chat, text: out.append((chat, text)) or True)
    return out


# ---------- Einheiten ----------
def test_units_krugerrand_is_one_ounce():
    assert units.coin_fine_grams("kruegerrand") == pytest.approx(units.OZ, rel=1e-3)
    assert units.coin_fine_grams("maple", 0.1) == pytest.approx(units.OZ * 0.1 * 0.9999, rel=1e-6)
    assert units.coin_fine_grams("vreneli") == pytest.approx(5.806, abs=0.01)


def test_price_per_gram_fineness():
    per_oz = 3110.34768  # → 100 je Gramm Feingold
    assert units.price_per(per_oz, "g", 1000) == pytest.approx(100)
    assert units.price_per(per_oz, "g", 585) == pytest.approx(58.5)
    assert units.price_per(per_oz, "kg", 999.9) == pytest.approx(99990)
    assert units.fmt_money(1234.6, "CHF") == "1.235 CHF"
    assert units.fmt_money(12.5) == "12,50 €"


# ---------- Profile ----------
def test_profile_pin_and_settings(con):
    p = profiles.create(con, "Anna", "1234", {"currency": "CHF", "gold": {"unit": "kg"}, "evil": 1})
    assert p["has_pin"] and p["settings"]["currency"] == "CHF" and p["settings"]["gold"]["unit"] == "kg"
    assert "evil" not in p["settings"]
    h = profiles.pin_hash(con, p["id"])
    assert profiles.check_pin("1234", h) and not profiles.check_pin("1235", h)
    with pytest.raises(profiles.ProfileError):
        profiles.create(con, "anna")  # Name schon vergeben (ohne Groß/klein)
    with pytest.raises(profiles.ProfileError):
        profiles.set_pin(con, p["id"], "12")
    s = profiles.update_settings(con, p["id"], {"currency": "XXX", "report": {"freq": "daily", "hour": 99, "weekday": "x"}})
    assert s["currency"] == "CHF" and s["report"]["freq"] == "daily" and s["report"]["hour"] == 23
    tok = profiles.make_session(p["id"])
    assert profiles.read_session(tok) == p["id"]
    assert profiles.read_session(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None


def test_holdings_normalize():
    h = profiles.normalize_holding({"asset": "GOLD", "form": "coin", "coin": "kruegerrand", "weight": 1, "count": 2})
    assert h["quantity"] == pytest.approx(2.0, rel=1e-3)
    h = profiles.normalize_holding({"asset": "GOLD", "form": "jewelry", "weight": 10, "unit": "g", "fineness": 585})
    assert h["quantity"] * units.OZ == pytest.approx(5.85)
    h = profiles.normalize_holding({"asset": "BTC", "quantity": 0.1})
    assert h["quantity"] == 0.1 and h["weight"] is None
    with pytest.raises(profiles.ProfileError):
        profiles.normalize_holding({"asset": "GOLD", "form": "bar", "weight": 0})
    with pytest.raises(profiles.ProfileError):
        profiles.normalize_holding({"asset": "BTC", "quantity": 1, "buy_date": "22.11.2026"})


# ---------- Bewertung und Rechner ----------
def test_portfolio_value_matches_price(con):
    p = profiles.create(con, "Bert", settings={"currency": "EUR"})
    profiles.save_holding(con, p["id"], {"asset": "GOLD", "form": "bar", "weight": 1, "unit": "oz", "fineness": 1000,
                                         "buy_date": "2022-01-03", "buy_price": 1600})
    pf = service.portfolio(con, profiles.get(con, p["id"]))
    price = service.live(con)["GOLD"]["eur"]
    assert pf["total"] == pytest.approx(price, rel=1e-9)
    assert pf["gain"] == pytest.approx(price - 1600, rel=1e-9)
    h = service.portfolio_history(con, profiles.get(con, p["id"]))
    assert not h["empty"] and h["invested_total"] == pytest.approx(1600)


def test_portfolio_other_currency(con):
    p = profiles.create(con, "Chris", settings={"currency": "CHF", "gold": {"unit": "g", "fineness": 585}})
    p = profiles.get(con, p["id"])
    eur = service.price_display(con, {**p, "settings": {**p["settings"], "currency": "EUR"}}, "GOLD")["value"]
    chf = service.price_display(con, p, "GOLD")["value"]
    assert chf / eur == pytest.approx(db.fx_now(con, "CHF"), rel=1e-6)
    assert eur == pytest.approx(service.live(con)["GOLD"]["eur"] / units.OZ * 0.585, rel=1e-9)


def test_calc_past_single_lump_sum(con):
    p = profiles.create(con, "Dora", settings={"currency": "EUR", "tax": "none"})
    p = profiles.get(con, p["id"])
    r = service.calc_past(con, p, {"initial": 1000, "monthly": 0, "start": "2015-01-01", "weights": {"MSCIW": 100},
                                   "costs": False, "tax": False})
    closes = service.closes_eur(con)["MSCIW"]
    expect = 1000 * closes.iloc[-1] / closes.asof(np.datetime64("2015-01-01"))
    assert r["final"] == pytest.approx(expect, rel=1e-6)
    # v3: verglichen wird mit der eigenen Auswahl plus den drei Bezugsgrößen, nicht mit allen 33 Märkten
    assert {c["asset"] for c in r["compare"]} == {"MSCIW", "GOLD", "BTC"}


def test_calc_goal_reaches_goal_in_median(con):
    p = profiles.create(con, "Emil", settings={"currency": "EUR", "tax": "none"})
    p = profiles.get(con, p["id"])
    body = {"goal": 50000, "years": 10, "weights": {"MSCIW": 100}, "drift": "careful", "costs": True}
    g = service.calc_goal(con, p, body)
    assert 0 < g["monthly_50"] < g["monthly_90"] < g["no_risk"] * 1.5
    f = service.calc_future(con, p, {**body, "initial": 0, "monthly": g["monthly_50"], "tax": False})
    assert f["end"][50] == pytest.approx(50000, rel=0.08)


def test_calc_future_with_holdings(con):
    p = profiles.create(con, "Fritz", settings={"currency": "EUR"})
    profiles.save_holding(con, p["id"], {"asset": "BTC", "quantity": 0.01, "buy_date": "2020-01-02"})
    p = profiles.get(con, p["id"])
    f = service.calc_future(con, p, {"initial": 0, "monthly": 50, "years": 5, "weights": {"GOLD": 100},
                                     "use_holdings": True})
    assert f["start_value"] == pytest.approx(service.live(con)["BTC"]["eur"] * 0.01, rel=1e-6)
    # erster Fan-Punkt: Bestand plus erste Rate (abzüglich Kaufgebühr)
    assert f["start_value"] < f["fan"]["p5"][0] <= f["start_value"] + 50
    assert len(f["rows"]) == 5


# ---------- Telegram ----------
def test_link_code_and_bot_start(con, sent):
    p = profiles.create(con, "Gina")
    code = profiles.new_link_code(con, p["id"])
    telegram.handle(con, {"chat": {"id": 4711}, "text": f"/start {code}"})
    assert profiles.get(con, p["id"])["tg_chat_id"] == "4711"
    assert "Verbunden" in sent[-1][1]
    telegram.handle(con, {"chat": {"id": 4711}, "text": f"/start {code}"})  # Code nur einmal gültig
    assert "ungültig" in sent[-1][1]
    telegram.handle(con, {"chat": {"id": 999}, "text": "/bericht"})  # fremder Chat
    assert "noch mit keinem Profil" in sent[-1][1]
    telegram.handle(con, {"chat": {"id": 4711}, "text": "/stop"})
    assert profiles.get(con, p["id"])["tg_chat_id"] is None


def test_dispatch_levels_and_daily_cap(con, sent):
    strong = profiles.create(con, "Hans", settings={"alerts": {"level": "strong", "max_per_day": 2}})
    normal = profiles.create(con, "Ida", settings={"alerts": {"level": "normal", "max_per_day": 1}})
    off = profiles.create(con, "Jo", settings={"alerts": {"level": "off"}})
    for i, pr in enumerate((strong, normal, off)):
        profiles.set_chat(con, pr["id"], f"chat{i}")
    ev = [{"asset": "GOLD", "rule": "move_24h", "level": "normal", "direction": "down", "r": -0.035, "p": 3000},
          {"asset": "BTC", "rule": "move_24h", "level": "strong", "direction": "up", "r": 0.12, "p": 70000},
          {"asset": "BTC", "rule": "move_24h", "level": "normal", "direction": "up", "r": 0.12, "p": 70000}]
    alerts.dispatch(con, ev)
    by_chat = {}
    for chat, text in sent:
        by_chat.setdefault(chat, []).append(text)
    assert len(by_chat.get("chat0", [])) == 1 and "Bitcoin" in by_chat["chat0"][0]  # nur „stark“
    assert len(by_chat.get("chat1", [])) == 1  # Tageslimit 1
    assert "chat2" not in by_chat


def test_report_text_uses_profile_units(con):
    p = profiles.create(con, "Kai", settings={"currency": "CHF", "gold": {"unit": "g", "fineness": 585}})
    t = telegram.report_text(con, profiles.get(con, p["id"]))
    assert "1 g Gold (585)" in t and "CHF" in t and "Wochenbericht" in t


# ---------- Web-API ----------
@pytest.fixture()
def client(dbcopy):
    from fastapi.testclient import TestClient

    from web.server import app

    return TestClient(app, headers={"X-Invest": "1"})


def test_api_flow(client):
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/profiles", json={"name": "Web", "pin": "4321", "settings": {"currency": "EUR"}})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get("/api/me").json()["name"] == "Web"
    h = client.post("/api/holdings", json={"asset": "SILVER", "form": "bar", "weight": 1, "unit": "kg", "fineness": 999})
    assert h.status_code == 200
    assert client.get("/api/portfolio").json()["total"] > 0
    for q, body in (("future", {"initial": 1000, "monthly": 50, "years": 3, "weights": {"GOLD": 100}}),
                    ("goal", {"goal": 10000, "years": 5, "weights": {"MSCIW": 100}}),
                    ("past", {"initial": 1000, "start": "2020-01-01", "weights": {"BTC": 50, "GOLD": 50}})):
        r = client.post(f"/api/calc/{q}", json=body)
        assert r.status_code == 200, r.text
    assert client.post("/api/calc/future", json={"weights": {}}).status_code == 400
    assert client.get("/api/market/GOLD?range=5y").status_code == 200
    assert client.get("/api/telegram/preview").status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"id": pid, "pin": "0000"}).status_code == 403
    assert client.post("/api/login", json={"id": pid, "pin": "4321"}).status_code == 200


def test_api_requires_custom_header_and_isolates_profiles(client):
    from fastapi.testclient import TestClient

    from web.server import app

    bare = TestClient(app)
    assert bare.post("/api/profiles", json={"name": "Böse"}).status_code == 403
    a = client.post("/api/profiles", json={"name": "Owner"}).json()
    hid = client.post("/api/holdings", json={"asset": "BTC", "quantity": 1}).json()["id"]
    other = TestClient(app, headers={"X-Invest": "1"})
    other.post("/api/profiles", json={"name": "Other"})
    other.delete(f"/api/holdings/{hid}")  # fremde Position: darf nichts löschen
    assert any(p["id"] == hid for p in client.get("/api/portfolio").json()["positions"])
    assert other.get("/api/portfolio").json()["empty"]
    assert a["name"] == "Owner"
