import numpy as np
import pytest

from invest.calculator import Costs, Plan, simulate
from invest.forecast import bootstrap_paths, custom_path

NO = {"BTC": Costs(), "GOLD": Costs(), "MSCIW": Costs()}


def flat(h, level=1.0):
    return np.full((1, h + 1), level)


def linear(h, end):
    return np.linspace(1, end, h + 1)[None, :]


def test_flat_no_costs_keeps_money():
    r = simulate(Plan({"BTC": 1}, 365, initial=100, costs=NO), {"BTC": flat(365)})
    assert r["after_tax"][50] == pytest.approx(100)
    assert r["profit"][50] == pytest.approx(0)


def test_double_price_doubles_money():
    r = simulate(Plan({"BTC": 1}, 365, initial=100, costs=NO), {"BTC": linear(365, 2)})
    assert r["after_tax"][50] == pytest.approx(200)


def test_fees_and_break_even():
    c = {"BTC": Costs(buy_fee=0.01)}
    r = simulate(Plan({"BTC": 1}, 365, initial=100, costs=c), {"BTC": flat(365)})
    assert r["payout"][50] == pytest.approx(100 * 0.99 ** 2)
    assert r["break_even_pct"] == pytest.approx(1 / 0.99 ** 2 - 1)


def test_savings_plan_counts_12_rates():
    r = simulate(Plan({"BTC": 1}, 365, initial=0, monthly=100, costs=NO), {"BTC": flat(365)})
    assert r["invested"] == pytest.approx(1200)
    assert r["after_tax"][50] == pytest.approx(1200)


def test_german_tax_holding_period():
    short = simulate(Plan({"BTC": 1}, 200, initial=10_000, costs=NO, tax="de", personal_rate=0.35),
                     {"BTC": linear(200, 2)})
    assert short["tax"][50] == pytest.approx(3500)
    long = simulate(Plan({"BTC": 1}, 400, initial=10_000, costs=NO, tax="de"), {"BTC": linear(400, 2)})
    assert long["tax"][50] == pytest.approx(0)


def test_german_tax_freigrenze():
    r = simulate(Plan({"BTC": 1}, 200, initial=900, costs=NO, tax="de"), {"BTC": linear(200, 2)})
    assert r["tax"][50] == pytest.approx(0)  # 900 € Gewinn < 1.000 € Freigrenze


def test_etf_tax_partial_exemption():
    r = simulate(Plan({"MSCIW": 1}, 400, initial=10_000, costs=NO, tax="de"), {"MSCIW": linear(400, 2)})
    assert r["tax"][50] == pytest.approx((10_000 * 0.7 - 1000) * 0.26375)


def test_withdrawal_runs_out():
    r = simulate(Plan({"BTC": 1}, 365, initial=1000, withdraw_monthly=100, withdraw_from_month=1, costs=NO),
                 {"BTC": flat(365)})
    assert r["withdrawn"][50] == pytest.approx(1000)
    assert r["ran_out_share"] == 1.0


def test_rebalancing_math():
    a = np.concatenate([np.linspace(1, 2, 366), np.linspace(2, 1, 366)[1:]])[None, :]
    b = flat(730)
    plan = dict(weights={"BTC": 0.5, "GOLD": 0.5}, horizon_days=730, initial=100, costs=NO)
    with_reb = simulate(Plan(**plan, rebalance="yearly"), {"BTC": a, "GOLD": b})
    without = simulate(Plan(**plan), {"BTC": a, "GOLD": b})
    assert with_reb["after_tax"][50] == pytest.approx(112.5, rel=1e-3)
    assert without["after_tax"][50] == pytest.approx(100, rel=1e-6)


def test_worst_moment_and_drawdown():
    path = np.concatenate([np.linspace(1, 0.4, 101), np.linspace(0.4, 1.2, 100)[1:]])[None, :]
    r = simulate(Plan({"BTC": 1}, 199, initial=100, costs=NO), {"BTC": path})
    assert r["worst_moment"][50] == pytest.approx(-0.6, abs=1e-6)
    assert r["max_dd"][50] == pytest.approx(-0.6, abs=1e-6)


def test_bootstrap_shape_and_zero_drift():
    rng = np.random.default_rng(1)
    R = rng.normal(0.002, 0.03, size=(3000, 2))
    p = bootstrap_paths(R, 365, 2000, drift=0.0, seed=2)
    assert p.shape == (2, 2000, 366)
    assert np.allclose(p[:, :, 0], 1)
    assert abs(np.median(np.log(p[0, :, -1]))) < 0.1  # ohne Trend ≈ seitwärts


def test_custom_path():
    p = custom_path([(-50, 3), (100, 12)], 600, 1)
    assert p[0, 0, 0] == pytest.approx(1)
    assert p[0, 0, 91] == pytest.approx(0.5, rel=1e-3)
    assert p[0, 0, -1] == pytest.approx(1.0, rel=1e-3)
