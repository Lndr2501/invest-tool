"""Rendert jede Seite einmal headless und prüft auf Fehler (braucht gefüllte Datenbank)."""
import os

import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")
PAGES = ["page_overview", "page_patterns", "page_forecast", "page_training", "page_alerts", "page_calculator"]


def run(page, setup=None):
    os.environ["INVEST_PAGE"] = page
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    if setup:
        setup(at)
    assert not at.exception, [e.message + "\n" + "\n".join(e.stack_trace) for e in at.exception]
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    run(page)


MODES = ["Zukunft simulieren", "Was wäre gewesen (alle Startzeitpunkte)", "Konkreter Start in der Vergangenheit",
         "Stresstest: größter Crash", "Eigenes Szenario"]


@pytest.mark.parametrize("mode", MODES)
def test_calculator_modes(mode):
    def setup(at):
        at.radio(key="calc_mode").set_value(mode)
        at.number_input(key="w_GOLD").set_value(40)
        at.number_input(key="w_BTC").set_value(60)
        next(b for b in at.button if b.label == "Berechnen").click()
        at.run()
        assert not at.exception, [e.message + "\n" + "\n".join(e.stack_trace) for e in at.exception]
        texts = " ".join(m.label + " " + str(m.value) for m in at.metric)
        assert "Typisch nach Steuern" in texts or "Ergebnis nach Steuern" in texts

    run("page_calculator", setup)
