# Invest-Tool

Finanz-Ratgeber fürs Heimnetz: Kurse sammeln (Bitcoin, Gold, Silber, MSCI World und 33 Märkte),
Prognosen und Stresstests, Bestand je Profil, 12 Rechner, Telegram-Alarme und ein KI-Ratgeber
(OpenAI-kompatibler Anbieter, z. B. TLLX). Website: FastAPI + Alpine.js + ECharts, Daten in SQLite (WAL).

Was gebaut ist und was als Nächstes kommt (v4 Haushalts-Finanzen): siehe [PLAN.md](PLAN.md).

## Einrichten

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m invest web        # Website auf Port 8501
```

Konfiguration liegt außerhalb des Repos in `/root/.config/invest-tool/`
(`settings.toml`, `telegram.env` mit `TELEGRAM_TOKEN` und `TELEGRAM_CHAT_ID`).
Die Datenbank liegt in `data/invest.sqlite`, abweichend per `INVEST_DB=/pfad/zur.sqlite`.
API-Keys für den KI-Ratgeber werden in der Oberfläche eingegeben und nur in der Datenbank gespeichert.

## Dienste

Die systemd-Units in `deploy/` nach `/etc/systemd/system/` kopieren:

- `invest-app` – Website
- `invest-bot` – Telegram-Bot
- `invest-collect.timer` – Kurse alle 5 min
- `invest-refresh.timer` – Tageskurse, Wechselkurse, Inflation
- `invest-backtest.timer` – Walk-Forward-Backtest

## Tests

```bash
INVEST_DB=/tmp/kopie.sqlite .venv/bin/python -m pytest -q tests -W ignore::DeprecationWarning
```

Die Tests brauchen eine Kopie der Datenbank mit Kursdaten. Sie leeren Profile, Bestand und Keys in der Kopie.

`legacy/` enthält die alte Streamlit-Oberfläche (v1), nur zum Nachschlagen.
