"""Die drei Simulations-Rechner mit eigener Oberfläche (echte Kurse, viele mögliche Verläufe)."""
from __future__ import annotations

from .. import service
from . import calculator, money_field, years_field

WEIGHTS = {"key": "weights", "label": "Aufteilung", "type": "weights", "default": {"MSCIW": 100},
           "help": "Markt-ID → Anteil in Prozent, z. B. {\"MSCIW\": 70, \"GOLD\": 20, \"BTC\": 10}"}
DRIFT = {"key": "drift", "label": "Trend-Annahme", "type": "select", "default": "careful",
         "options": [["careful", "vorsichtig"], ["historic", "wie bisher"], ["none", "ohne Trend"]],
         "help": "vorsichtig = halber bis voller bisheriger Trend, gedeckelt pro Jahr"}
HOLD = {"key": "use_holdings", "label": "Meinen Bestand als Start nehmen", "type": "toggle", "default": False}


@calculator("future", "Was könnte daraus werden?", "Wie entwickelt sich ein Betrag oder eine Sparrate – im guten, "
            "mittleren und schlechten Fall?", "build", [
                money_field("initial", "Startbetrag", 1000), money_field("monthly", "Sparrate pro Monat", 100),
                years_field("years", "Dauer in Jahren", 10, hi=40), WEIGHTS, DRIFT, HOLD,
            ], ui="custom", icon="📈", order=10)
def future(con, profile, v):
    return service.calc_future(con, profile, v)


@calculator("goal", "Wie viel muss ich sparen?", "Welche Sparrate brauche ich für meinen Zielbetrag?", "build", [
    money_field("goal", "Zielbetrag", 50000), years_field("years", "In wie vielen Jahren?", 10, hi=40),
    money_field("initial", "Schon vorhanden", 0), WEIGHTS, DRIFT, HOLD,
], ui="custom", icon="🎯", order=20)
def goal(con, profile, v):
    return service.calc_goal(con, profile, v)


@calculator("past", "Was wäre gewesen?", "Was wäre aus einem Betrag oder Sparplan mit echten Kursen geworden?",
            "past", [
                money_field("initial", "Startbetrag", 1000), money_field("monthly", "Sparrate pro Monat", 50),
                {"key": "start", "label": "Start am", "type": "date", "default": "2015-01-01"}, WEIGHTS,
            ], ui="custom", icon="⏪", order=10)
def past(con, profile, v):
    return service.calc_past(con, profile, v)
