"""Tests für v3: Markt-Katalog, Steuer 2026, Rechner-Baukasten, Start-Assistent, KI-Ratgeber und neue Web-API.

Läuft auf einer Kopie der Datenbank aus ``INVEST_DB`` (Kurse werden gebraucht), nie auf dem Original.
Der KI-Ratgeber spricht mit einem lokalen Test-Server (tests/mock_openai.py), nie mit einem echten Modell.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from invest import advice, assistant, calcs, config, db, markets, profiles, service, sources, taxde
from tests import mock_openai


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
    for t in ("holdings", "plans", "tg_links", "tg_log", "profiles", "secrets", "ai_messages"):
        con.execute(f"DELETE FROM {t}")
    con.execute("DELETE FROM kv WHERE key LIKE 'ai:%' OR key = 'admin_profile_id'")
    con.commit()
    con.close()
    service.clear_cache()
    yield path
    db.DB_PATH = old
    service.clear_cache()


@pytest.fixture()
def con(dbcopy):
    c = db.connect()
    markets.sync(c)
    yield c
    c.close()


_N = iter(range(1, 10_000))


@pytest.fixture()
def profile(con):
    p = profiles.create(con, f"Rechner {next(_N)}")
    return profiles.get(con, p["id"])


@pytest.fixture(scope="module")
def mock():
    srv, url = mock_openai.start()
    yield url
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_openai.REQUESTS.clear()
    mock_openai.NO_TOOLS["flag"] = False
    mock_openai.NO_REASONING["flag"] = False


# ---------- Märkte ----------
def test_catalog_has_all_kinds():
    kinds = {markets.kind_of(a) for a in markets.CATALOG}
    assert {"crypto", "metal", "stock", "etf", "bond", "fund"} <= kinds
    assert len(markets.CATALOG) >= 33
    for a in ("PLATINUM", "PALLADIUM", "GOLD", "SILVER"):
        assert markets.is_metal(a)
    assert not markets.is_metal("MSCIW")
    for a in markets.CATALOG:
        pub = markets.public(a)
        assert pub["id"] == a and pub["name"] and pub["kind"] in markets.KINDS


def test_costs_differ_by_kind():
    etf, coin = markets.costs_for("MSCIW"), markets.costs_for("BTC")
    assert etf != coin
    assert markets.cap_pa("BTC") >= markets.cap_pa("MSCIW")


def test_search_catalog_without_network(monkeypatch):
    monkeypatch.setattr(sources, "yahoo_search", lambda q, n: (_ for _ in ()).throw(OSError("offline")))
    hits = markets.search("silber")
    assert hits and hits[0]["id"] == "SILVER" and hits[0]["known"]
    assert markets.search("x") == []


def test_add_custom_market_is_persisted_and_removable(con, monkeypatch):
    rows = [object()] * 120
    meta = {"instrumentType": "EQUITY", "currency": "USD", "longName": "Testfirma AG"}
    monkeypatch.setattr(sources, "yahoo_chart", lambda *a, **k: (rows, meta))
    m = markets.add_custom(con, "tst.de")
    assert m["id"] == "U_TST_DE" and m["kind"] == "stock" and m["name"] == "Testfirma AG"
    assert "U_TST_DE" in config.ASSETS
    assert markets.add_custom(con, "TST.DE")["id"] == "U_TST_DE"  # zweites Mal: nichts doppelt
    with pytest.raises(ValueError):
        markets.add_custom(con, "bad symbol!")
    con.execute("DELETE FROM markets WHERE id='U_TST_DE'")
    con.commit()
    markets.sync(con)
    assert "U_TST_DE" not in config.ASSETS


def test_add_custom_rejects_short_history(con, monkeypatch):
    monkeypatch.setattr(sources, "yahoo_chart", lambda *a, **k: ([object()] * 10, {}))
    with pytest.raises(ValueError, match="zu wenig Kursdaten"):
        markets.add_custom(con, "NEW1")


# ---------- Steuer 2026 (§ 32a EStG) ----------
def test_income_tax_2026_corner_points():
    assert taxde.income_tax_single(12_348) == 0
    assert taxde.income_tax_single(12_349) == 0  # erste Euro über dem Grundfreibetrag: noch unter 1 €
    assert taxde.income_tax_single(40_000) == 7_209
    # Grenzsteuersatz oben: 42 % bzw. 45 % (ohne Soli)
    assert taxde.income_tax_single(101_000) - taxde.income_tax_single(100_000) == pytest.approx(420, abs=1)
    assert taxde.income_tax_single(301_000) - taxde.income_tax_single(300_000) == pytest.approx(450, abs=1)
    assert taxde.income_tax(80_000, married=True) == 2 * taxde.income_tax_single(40_000)


def test_soli_and_capital_rate():
    assert taxde.soli(20_350) == 0
    assert taxde.soli(40_000) == pytest.approx(2_200)
    assert taxde.soli(40_000, married=True) == 0
    assert taxde.capital_rate() == pytest.approx(0.26375)
    assert taxde.capital_rate(0.09) == pytest.approx(0.27995, abs=1e-4)  # 25/(1+0,25·0,09) + Soli + KiSt
    r = taxde.rates(50_000)
    assert 0.15 < r["average"] < r["marginal"] < 0.45


# ---------- Rechner-Baukasten ----------
def test_calculator_catalog():
    ids = [c["id"] for c in calcs.catalog()]
    for cid in ("future", "goal", "past", "compound", "inflation", "budget", "retirement", "withdraw",
                "taxrate", "gaintax", "costs", "loss"):
        assert cid in ids
    assert len(ids) == len(set(ids))
    for c in calcs.catalog():
        assert c["title"] and c["question"] and c["category"]
        for f in c["fields"]:
            assert f["key"] and f["type"]


@pytest.mark.parametrize("cid", ["compound", "inflation", "budget", "retirement", "withdraw", "taxrate",
                                 "gaintax", "costs", "loss"])
def test_generic_calculators_run_with_defaults(con, profile, cid):
    r = calcs.run(con, profile, cid, calcs.defaults(cid, profile))
    assert r["id"] == cid
    assert r.get("tiles") or r.get("text")
    json.dumps(r)  # muss als JSON an die Seite gehen
    for ch in r.get("charts", []):
        assert ch["type"] in ("line", "stack", "bar", "hbar")


def test_calculators_clamp_and_reject_bad_input(con, profile):
    r = calcs.run(con, profile, "compound", {"years": 10_000})
    assert r["id"] == "compound"
    with pytest.raises(calcs.CalcError):
        calcs.run(con, profile, "compound", {"years": "viel"})
    with pytest.raises(calcs.CalcError):
        calcs.run(con, profile, "gibtsnicht", {})


def test_taxrate_calculator_matches_taxde(con, profile):
    r = calcs.run(con, profile, "taxrate", {"income_mode": "zve", "zve": 40_000})
    blob = json.dumps(r, ensure_ascii=False)
    assert "Grenzsteuersatz" in blob or "Grenz" in blob


def test_simulations_accept_many_markets(con, profile):
    w = {"MSCIW": 40, "GOLD": 20, "BTC": 10, "ETH": 10, "SAP": 10, "EUGOV": 10}
    r = calcs.run(con, profile, "future", {"initial": 1000, "monthly": 50, "years": 5, "weights": w})
    assert 0 < r["end"][5] < r["end"][50] < r["end"][95]
    assert set(r["assumptions"]["weights"]) == set(w)
    r = calcs.run(con, profile, "past", {"initial": 1000, "monthly": 50, "start": "2019-01-01", "weights": w})
    assert r["final"] > 0


# ---------- Start-Assistent ----------
def _answers(**kw):
    a = {"purpose": "wealth", "years": 20, "monthly": 500, "income": 3000, "expenses": 1800, "cash": 6000,
         "debt": 0, "reaction": "wait", "experience": "none"}
    return a | kw


def test_wizard_long_horizon_world_plus_gold(con, profile):
    s = advice.suggest(con, profile, _answers())
    assert s["horizon"] == "long"
    assert s["weights"] == {"MSCIW": 90, "GOLD": 10}
    assert sum(m["pct"] for m in s["mix"]) == 100
    assert s["forecast"]["p5"] < s["forecast"]["p50"] < s["forecast"]["p95"]


def test_wizard_short_horizon_is_cash(con, profile):
    s = advice.suggest(con, profile, _answers(years=2, purpose="purchase"))
    assert s["horizon"] == "short"
    assert s["forecast"]["kind"] == "cash"
    assert not any(m["asset"] in ("BTC", "MSCIW") for m in s["mix"])


def test_wizard_mid_horizon_worried_is_mostly_bonds(con, profile):
    s = advice.suggest(con, profile, _answers(years=7, reaction="worry"))
    assert s["horizon"] == "mid"
    assert s["weights"].get("EUGOV", 0) > s["weights"].get("MSCIW", 0)


def test_wizard_order_debt_then_emergency(con, profile):
    s = advice.suggest(con, profile, _answers(debt=3000, cash=0))
    titles = [x["title"].lower() for x in s["steps"]]
    assert "schulden" in titles[0]
    assert any("notgroschen" in t for t in titles[1:])


def test_wizard_derives_monthly_from_income(con, profile):
    s = advice.suggest(con, profile, _answers(monthly=None))
    assert s["monthly"] > 0 and s["monthly_source"] != "given"


# ---------- Web-API ----------
@pytest.fixture()
def client(dbcopy):
    from fastapi.testclient import TestClient

    from web.server import app

    return TestClient(app, headers={"X-Invest": "1"})


def _new(name):
    from fastapi.testclient import TestClient

    from web.server import app

    c = TestClient(app, headers={"X-Invest": "1"})
    r = c.post("/api/profiles", json={"name": f"{name} {next(_N)}"})
    assert r.status_code == 200, r.text
    return c


def test_api_meta_and_markets(client):
    _new_c = _new("Meta")
    m = _new_c.get("/api/meta").json()
    for k in ("assets", "kinds", "metals", "goal_purposes", "calc_categories"):
        assert m[k], k
    assert "PLATINUM" in m["metals"]
    lst = _new_c.get("/api/markets").json()
    assert len(lst["markets"] if isinstance(lst, dict) else lst) >= 33
    assert _new_c.put("/api/watch/ETH", json={"watch": True}).status_code == 200
    assert "ETH" in _new_c.get("/api/me").json()["settings"]["assets"]
    assert _new_c.put("/api/watch/ETH", json={"watch": False}).status_code == 200
    assert "ETH" not in _new_c.get("/api/me").json()["settings"]["assets"]
    assert _new_c.put("/api/watch/NOPE", json={"watch": True}).status_code in (400, 404)


def test_api_calcs_start_finance_tax():
    c = _new("Planer")
    assert len(c.get("/api/calcs").json()["calcs"]) >= 12
    r = c.post("/api/calc/budget", json={"income": 2500, "fixed": 1200, "living": 600})
    assert r.status_code == 200, r.text
    assert c.post("/api/calc/gibtsnicht", json={}).status_code in (400, 404)
    assert c.get("/api/tax?zve=40000").json()["tax"] == pytest.approx(7_209 + 0, abs=1)
    r = c.post("/api/start", json=_answers())
    assert r.status_code == 200, r.text
    assert r.json()["weights"] == {"MSCIW": 90, "GOLD": 10}
    f = c.get("/api/finance").json()
    for k in ("finance", "goals", "emergency", "personal_rate"):
        assert k in f


def test_api_me_cannot_set_ai_settings():
    c = _new("Schlau")
    c.put("/api/me", json={"settings": {"ai": {"mode": "own", "base_url": "http://evil.example"}}})
    assert (c.get("/api/me").json()["settings"].get("ai") or {}).get("base_url", "") != "http://evil.example"


# ---------- KI-Ratgeber ----------
def _admin_and_user(con):
    """Erstes Profil = Admin. Gibt (admin_client, user_client) zurück."""
    con.execute("DELETE FROM profiles")
    con.execute("DELETE FROM secrets")
    con.execute("DELETE FROM kv WHERE key LIKE 'ai:%' OR key = 'admin_profile_id'")
    con.commit()
    return _new("Admin"), _new("Nutzer")


def test_ai_global_only_admin_and_key_never_leaves(con, mock):
    admin, user = _admin_and_user(con)
    body = {"enabled": True, "base_url": mock, "model": "test-model", "api": "chat", "key": "sk-test"}
    assert user.put("/api/ai/global", json=body).status_code == 403
    r = admin.put("/api/ai/global", json=body)
    assert r.status_code == 200, r.text
    assert "sk-test" not in r.text
    assert r.json()["global"]["has_key"] and r.json()["is_admin"]
    u = user.get("/api/ai")
    assert "sk-test" not in u.text
    assert u.json()["effective"]["ready"] and not u.json()["is_admin"]
    t = admin.post("/api/ai/test", json={"scope": "global"}).json()
    assert t["ok"] and "test-model" in t["models"]
    assert user.post("/api/ai/test", json={"scope": "global"}).status_code == 403
    # neue Adresse → alter Key wird gelöscht (nie an fremde Server schicken)
    r = admin.put("/api/ai/global", json={"base_url": mock + "/"})
    assert not r.json()["global"]["has_key"]


def test_ai_own_key_and_consent(con):
    _, user = _admin_and_user(con)
    r = user.put("/api/ai/own", json={"mode": "own", "base_url": "https://8.8.8.8/v1", "model": "m", "key": "sk-own"})
    assert r.status_code == 200 and "sk-own" not in r.text
    e = r.json()["effective"]
    assert e["scope"] == "own" and not e["local"] and not e["ready"] and "zustimmen" in e["reason"]
    assert r.json()["own"]["has_key"]
    e = user.put("/api/ai/own", json={"consent": True}).json()["effective"]
    assert e["ready"]
    assert not user.put("/api/ai/own", json={"base_url": "https://8.8.4.4/v1"}).json()["own"]["has_key"]
    assert user.put("/api/ai/own", json={"base_url": "ftp://x"}).status_code == 400


def test_is_local():
    assert assistant.is_local("http://192.168.178.62:8000/v1")
    assert assistant.is_local("http://127.0.0.1:1/v1")
    assert assistant.is_local("http://nas.local/v1")
    assert not assistant.is_local("https://8.8.8.8/v1")


def test_think_filter_across_chunks():
    f = assistant._ThinkFilter()
    out = "".join(f.feed(s) for s in ["Hallo <thi", "nk>geheim", "</think> Welt", "<think>x</think>!"]) + f.flush()
    assert out.replace("  ", " ").strip() == "Hallo Welt!"


def _events(resp) -> list[dict]:
    return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_ai_chat_uses_tools_and_streams(con, mock, api):
    admin, user = _admin_and_user(con)
    admin.put("/api/ai/global", json={"enabled": True, "base_url": mock, "model": "test-model", "api": api,
                                      "key": "sk-test"})
    r = user.post("/api/ai/chat", json={"message": "Ich verdiene 2800 netto, Ausgaben 1500.", "page": "Seite ueberblick."})
    assert r.status_code == 200
    ev = _events(r)
    types = [e["type"] for e in ev]
    assert "saved" in types and types[-1] == "done", types
    text = "".join(e["text"] for e in ev if e["type"] == "delta")
    assert "Wofür sparst du?" in text and "think" not in text and "überlege" not in text
    fin = user.get("/api/me").json()["settings"]["finance"]
    assert fin["income"] == 2800 and fin["expenses"] == 1500
    # Kontext: System-Prompt mit Profil, Werkzeuge mitgeschickt, Key im Header
    first = mock_openai.REQUESTS[0]
    assert first["auth"] == "Bearer sk-test" and first["body"]["tools"]
    sys_text = json.dumps(first["body"], ensure_ascii=False)
    assert "Seite ueberblick" in sys_text
    if api == "responses":
        second = mock_openai.REQUESTS[1]["body"]["input"]
        assert any(i.get("type") == "function_call" for i in second)
        assert any(i.get("type") == "function_call_output" for i in second)
    hist = user.get("/api/ai/history").json()["messages"]
    assert [m["role"] for m in hist][-2:] == ["user", "assistant"]
    assert user.delete("/api/ai/history").status_code == 200
    assert user.get("/api/ai/history").json()["messages"] == []


def test_ai_chat_without_tool_support(con, mock):
    admin, user = _admin_and_user(con)
    admin.put("/api/ai/global", json={"enabled": True, "base_url": mock, "model": "test-model", "key": "sk-test"})
    mock_openai.NO_TOOLS["flag"] = True
    ev = _events(user.post("/api/ai/chat", json={"message": "Hallo"}))
    assert ev[-1]["type"] == "done"
    assert "Wofür sparst du?" in "".join(e["text"] for e in ev if e["type"] == "delta")
    assert not mock_openai.REQUESTS[-1]["body"].get("tools")


def test_ai_chat_not_ready_explains(con):
    _, user = _admin_and_user(con)
    ev = _events(user.post("/api/ai/chat", json={"message": "Hallo"}))
    assert ev[0]["type"] == "error" and "eingerichtet" in ev[0]["text"]


def test_ai_tools_direct(con, profile):
    res, ev = assistant.run_tool(con, profile, "run_calculator", {"id": "budget", "inputs": {"income": 2000}})
    assert ev and ev["type"] == "calc"
    res, ev = assistant.run_tool(con, profile, "update_finance_profile", {"fields": {"risk": 9, "income": -5}})
    stored = profiles.get(con, profile["id"])["settings"]["finance"]
    assert stored.get("income") != -5 and stored.get("risk") != 9
    res, _ = assistant.run_tool(con, profile, "gibtsnicht", {})
    assert "error" in res
    snap = assistant.snapshot(con, profiles.get(con, profile["id"]))
    assert "Bestand" in snap or "bestand" in snap


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_ai_effort_and_token_limit_are_sent(con, mock, api):
    admin, user = _admin_and_user(con)
    r = admin.put("/api/ai/global", json={"enabled": True, "base_url": mock, "model": "test-model", "api": api,
                                          "key": "sk-test", "effort": "low", "max_tokens": 3000})
    assert r.json()["global"]["effort"] == "low" and r.json()["global"]["max_tokens"] == 3000
    _events(user.post("/api/ai/chat", json={"message": "Hallo"}))
    body = mock_openai.REQUESTS[0]["body"]
    if api == "responses":
        assert body["reasoning"] == {"effort": "low"} and body["max_output_tokens"] == 3000
    else:
        assert body["reasoning_effort"] == "low" and body["max_tokens"] == 3000
    # ungültige Werte: Denktiefe bleibt, Limit wird begrenzt
    g = admin.put("/api/ai/global", json={"effort": "turbo", "max_tokens": 10**9}).json()["global"]
    assert g["effort"] == "low" and g["max_tokens"] == 64000
    assert admin.put("/api/ai/global", json={"max_tokens": "viel"}).status_code == 400


def test_ai_retries_without_effort_when_rejected(con, mock):
    admin, user = _admin_and_user(con)
    admin.put("/api/ai/global", json={"enabled": True, "base_url": mock, "model": "test-model", "api": "responses",
                                      "key": "sk-test", "effort": "medium"})
    mock_openai.NO_REASONING["flag"] = True
    ev = _events(user.post("/api/ai/chat", json={"message": "Hallo"}))
    assert ev[-1]["type"] == "done"
    assert "Wofür sparst du?" in "".join(e["text"] for e in ev if e["type"] == "delta")


def test_ai_own_effort(con):
    _, user = _admin_and_user(con)
    r = user.put("/api/ai/own", json={"mode": "own", "base_url": "http://127.0.0.1:1/v1", "model": "m",
                                      "effort": "xhigh", "max_tokens": 12000})
    e = r.json()["effective"]
    assert e["effort"] == "xhigh" and e["max_tokens"] == 12000
