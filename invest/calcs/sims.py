"""Die drei Simulations-Rechner mit eigener Oberfläche (echte Kurse, viele mögliche Verläufe)."""
from __future__ import annotations

from .. import service
from . import calculator, money_field, years_field

WEIGHTS = {"key": "weights", "label": "Mischung", "type": "weights", "default": {"MSCIW": 100},
           "help": "Wie dein Geld auf die Anlagen verteilt ist, in Prozent. Beispiel: 70 % Welt-ETF, 20 % Gold, "
                   "10 % Bitcoin ({\"MSCIW\": 70, \"GOLD\": 20, \"BTC\": 10})."}
DRIFT = {"key": "drift", "label": "Trend-Annahme", "type": "select", "default": "careful",
         "options": [["careful", "vorsichtig (empfohlen)"], ["historic", "wie in der Vergangenheit"],
                     ["none", "ohne Trend, nur Schwankung"]],
         "help": "Wie stark die Kurse im Schnitt steigen. „Vorsichtig“ nimmt weniger Wachstum an als früher und "
                 "ist pro Jahr gedeckelt. „Ohne Trend“ zeigt nur das Auf und Ab."}
HOLD = {"key": "use_holdings", "label": "Meinen Bestand dazunehmen", "type": "toggle", "default": False}


@calculator("future", "Was könnte daraus werden?", "Was kann aus meinem Geld werden – im guten, mittleren und "
            "schlechten Fall?", "build", [
                money_field("initial", "Startbetrag", 1000,
                            "Geld, das du am Anfang auf einmal anlegst. 0, wenn du nur monatlich sparst."),
                money_field("monthly", "Sparrate pro Monat", 100,
                            "Was du jeden Monat dazu anlegst. Automatisch geht das mit einem Sparplan bei der Bank."),
                years_field("years", "Dauer in Jahren", 10, "Wie lange das Geld liegen bleibt. Je länger, desto eher "
                            "gleichen sich Schwankungen aus, sicher ist es aber nie.", hi=40), WEIGHTS, DRIFT, HOLD,
            ], ui="custom", icon="📈", order=10)
def future(con, profile, v):
    return service.calc_future(con, profile, v)


@calculator("goal", "Wie viel muss ich sparen?", "Wie viel muss ich jeden Monat anlegen, um mein Ziel zu erreichen?",
            "build", [
                money_field("goal", "Zielbetrag", 50000,
                            "Wie viel du am Ende haben möchtest, z. B. für ein Auto, eine Wohnung oder als Rücklage."),
                years_field("years", "In wie vielen Jahren?", 10, hi=40),
                money_field("initial", "Schon vorhanden", 0, "Geld, das du heute schon dafür anlegen kannst. Sonst 0."),
                WEIGHTS, DRIFT, HOLD,
            ], ui="custom", icon="🎯", order=20)
def goal(con, profile, v):
    return service.calc_goal(con, profile, v)


@calculator("past", "Was wäre gewesen?", "Was wäre aus meinem Geld geworden, wenn ich früher angelegt hätte?",
            "past", [
                money_field("initial", "Startbetrag", 1000,
                            "Geld, das du am Anfang auf einmal anlegst. 0, wenn du nur monatlich sparst."),
                money_field("monthly", "Sparrate pro Monat", 50,
                            "Was du jeden Monat dazu anlegst. Automatisch geht das mit einem Sparplan bei der Bank."),
                {"key": "start", "label": "Start am", "type": "date", "default": "2015-01-01"}, WEIGHTS,
            ], ui="custom", icon="⏪", order=10)
def past(con, profile, v):
    return service.calc_past(con, profile, v)
