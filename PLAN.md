# Invest-Tool – Plan

Für Lndr und Freunde im Heimnetz. Läuft auf remote-vibe, Website im LAN: http://192.168.178.72:8501

Assets: Bitcoin, Gold, Silber, MSCI World (ETF). Intern EUR (Metalle je Feinunze), Anzeige je Profil in
11 Währungen (EZB-Kurse) und Gold/Silber je Gramm, Kilo oder Unze in wählbarer Feinheit.

## Schritte

- [x] 0. Gerüst: venv, Pakete, Konfiguration, Datenbank (SQLite im WAL-Modus, damit Sammler und Oberfläche gleichzeitig zugreifen)
- [x] 1. Sammler: komplette Historie laden (täglich ab 2000/2014, 5 min ab 2017/2020), danach alle 5 min neue Kerzen, stündlich Tageskurse, Wechselkurs und Inflation
- [x] 2. Musteranalyse: Trend, Schwankung inkl. GARCH-Prognose, Marktphase (Markov-Regime), Absturz vom Hoch, Zusammenhang zwischen Assets, Ausreißer im Minutenbereich
- [x] 3. Prognose: Monte Carlo (Block-Bootstrap, gemeinsam für alle Assets) und GARCH-Simulation (aktuelle Schwankung)
- [x] 4. Rechner: Einmal, Sparplan, Entnahme, Ziel, Portfolio-Mix mit Rebalancing, Kosten, Steuern (DE vereinfacht), Inflation, historisch, Stresstest, eigenes Szenario
- [x] 5. Training: Walk-Forward-Backtest – Treffen die Prognose-Spannen? Schlagen Richtungsmodelle eine simple Basis nach Gebühren?
- [x] 6. Telegram-Alarm: Sturz/Sprung in 1 h und 24 h, ungewöhnliche Bewegung, neues Allzeithoch, Wechsel der Marktphase, Sperrzeit gegen Spam
- [x] 7. Oberfläche (Streamlit) und systemd-Dienste und Timer, Endtest

### v2 (22.11.2026): Profile, Ratgeber-Website, Bestand

- [x] Profile (Name, optional PIN 4–12 Ziffern, Sitzungs-Cookie 180 Tage); jedes Profil sieht nur seinen Bestand.
  Lndrs v1-Daten wurden automatisch als Profil „Lndr“ mit verknüpftem Telegram übernommen.
- [x] Bestand: Münzen (Philharmoniker, Krügerrand, Vreneli, 10-DM …), Barren, Schmuck mit Feinheit, BTC, ETF-Anteile,
  Sparpläne; Wert, Gewinn, Verlauf seit Kauf.
- [x] Rechner als drei Fragen: „Was könnte daraus werden?“, „Wie viel muss ich sparen?“, „Was wäre gewesen?“ –
  mit Startbestand, Sätzen statt Fachbegriffen, Härtetest, Vergleich „alles in eine Anlage“.
- [x] Telegram je Profil: Bericht (aus/täglich/wöchentlich/monatlich, Tag, Uhrzeit, Inhalte), Alarme (aus/nur sehr
  große/große Bewegungen, Regeln, Höchstzahl pro Tag), Verknüpfen per Link in den Einstellungen.
- [x] Bot-Befehle: /bericht, /preis, /bestand, /stop, /hilfe.

## Datenquellen (getestet 22.11.2026, ohne Key)

| Asset | 5 min | Täglich |
|---|---|---|
| Bitcoin | Binance BTCEUR (ab 2020-01), Ersatz Kraken XBTEUR | Yahoo BTC-EUR (ab 2014-09) |
| Gold | Binance PAXGUSDT (ab 2020-08), Ersatz Kraken PAXGEUR; Spot: gold-api.com XAU | Yahoo GC=F (ab 2000) |
| Silber | Yahoo SI=F (60 Tage, danach eigenes Archiv); Spot: gold-api.com XAG | Yahoo SI=F (ab 2000) |
| MSCI World | Yahoo EUNL.DE (60 Tage, danach eigenes Archiv) | Yahoo EUNL.DE (ab 2009) |
| Wechselkurse (11 Währungen) | – | EZB (ab 1999) |
| Inflation DE, AT, CH, Euroraum | – | Eurostat HVPI prc_hicp_minr (monatlich ab 1996, CH ab 2004) |

## Befehle

```
.venv/bin/python -m invest backfill   # komplette Historie (einmalig, ~5 min)
.venv/bin/python -m invest collect    # 5-min-Lauf (Timer)
.venv/bin/python -m invest refresh    # stündlich: Tageskurse, FX, Inflation, Analyse
.venv/bin/python -m invest backtest   # nachts: Training/Backtest
.venv/bin/python -m invest status     # Datenstand
.venv/bin/python -m invest web        # Website (Dienst invest-app), Standard 192.168.178.72:8501
.venv/bin/python -m invest bot        # Telegram-Bot (Dienst invest-bot)
INVEST_DB=/pfad/kopie.sqlite .venv/bin/python -m invest web 127.0.0.1 8502   # Testserver auf DB-Kopie
```

Code: `invest/` (Daten, Analyse, Rechner, `service.py` = Logik für die Website, `profiles.py`, `telegram.py`,
`alerts.py`), `web/` (FastAPI `server.py`, Oberfläche `static/` mit Alpine.js + ECharts, lokal ausgeliefert).
Streamlit-v1 liegt nur noch als Archiv in `legacy/`.

## Stand 22.11.2026 (v2)

- Alles läuft als systemd-Timer: `invest-collect.timer` (alle 5 min, inkl. Alarme und fällige Berichte),
  `invest-refresh.timer` (stündlich :07), `invest-backtest.timer` (03:20). Dienste: `invest-app.service`
  (Website, FastAPI) → http://192.168.178.72:8501 (nur LAN), `invest-bot.service` (Telegram-Befehle).
- 26 Tests grün (`.venv/bin/python -m pytest -q tests`), davon 14 für v2 auf einer DB-Kopie.
- „Vorsichtige“ Prognose deckelt den Trend pro Jahr (BTC 15 %, Gold 5 %, Silber 4 %, MSCI World 7 %),
  sonst rechnet der Bootstrap mit BTC-Vergangenheit Fantasiewerte hoch.
- Backtest-Ergebnis: Prognose-Spannen gut kalibriert (90-%-Band traf 84–94 %). Beste Trend-Annahme: BTC halber
  Trend, sonst voller Trend. Kein Richtungsmodell schlägt „steigt immer“ nach Gebühren → keine Richtungsprognose.
- Telegram: eigener Bot (Token in `/root/.config/invest-tool/telegram.env`, Rechte 600). Chat-IDs je
  Profil in der DB; Verknüpfen über Einstellungen → „Telegram verbinden“ (Link mit Einmal-Code, 24 h gültig).

## Stand 22.11.2026 (v3)

- **Märkte modular:** `invest/markets.py` = Katalog mit 33 Märkten (ETFs, Krypto, 4 Edelmetalle, Aktien,
  Anleihen, Fonds). Neuer Markt = ein Eintrag im Katalog. Nutzer können über Märkte → Suche jedes Yahoo-Kürzel
  hinzufügen (Tabelle `markets`, IDs `U_<KÜRZEL>`, mindestens 60 Tage Historie).
- **Rechner-Baukasten:** `invest/calcs/` – ein Rechner = eine Funktion mit `@calculator(...)` und Feldliste;
  die Seite baut Formular und Ergebnis (Kacheln, Text, Diagramme, Tabelle, Aktionen) automatisch. 12 Rechner:
  future, goal, past (echte Kurse, beliebig viele Märkte), compound, inflation, budget, retirement, withdraw,
  taxrate, gaintax (§ 23 / Abgeltungsteuer), costs, loss. Steuer 2026 nach § 32a EStG in `invest/taxde.py`.
- **Einsteiger:** Überblick zeigt nur Lage-Satz, Bestand/Leerzustand, einen nächsten Schritt, Märkte, Frage-Chips.
  Start-Assistent (`#/start`, `invest/advice.py`): 5 Fragen → Reihenfolge (Schulden → Notgroschen → anlegen),
  Sparrate, Mischung nach Zeitraum und Risiko, Beispielrechnung. Leerer Bestand bietet 4 Einstiege.
- **KI-Ratgeber** (`invest/assistant.py`): OpenAI-kompatibel, Chat Completions oder Responses API, Streaming,
  Werkzeuge (Überblick, Markt, Rechner ausführen, Finanzprofil speichern, Markt beobachten), `<think>` wird
  ausgefiltert, ohne Werkzeug-Unterstützung läuft er als reiner Chat weiter.
  - Gemeinsamer Zugang: nur das Admin-Profil (erstes Profil) unter Einstellungen → KI-Ratgeber. Jedes Profil
    kann stattdessen einen eigenen Zugang eintragen.
  - Schlüssel liegen in der Tabelle `secrets`, gehen nie an den Browser und werden bei neuer Adresse gelöscht.
  - Server außerhalb des Heimnetzes nur mit Zustimmung des Profils.
- Frontend aufgeteilt: `app.js` (Kern, `part()`-Registrierung), `pages.js`, `calc.js`, `charts.js`, `chat.js`.
- 63 Tests grün: `INVEST_DB=<kopie> .venv/bin/python -m pytest -q tests` (v3: `tests/test_v3.py`, KI gegen
  `tests/mock_openai.py`, nie gegen ein echtes Modell).
- Sicherung vor dem Umstieg: `legacy/backups/` (Code v2 + Datenbank-Kopie vom 22.11.2026).
- Entwicklung in `/root/invest-tool-next`, Umstieg per `rsync` (ohne `.venv`, `data`, `legacy`), dann
  `systemctl restart invest-app invest-bot`.

## Plan v4 (ab 22.11.2026): Haushalts-Finanzen wie Finanzguru

Ziel: eine verlässliche Finanz-App für den Alltag – Konten, Einnahmen, Ausgaben, Verträge, Budgets –
verbunden mit dem Anlage-Teil aus v1–v3. Vorbild Finanzguru: Konten, Kategorien, Vertragsordner mit
Fristen-Wecker, Budgets, Kontostand-Prognose bis zum nächsten Gehalt.

Datenquelle zuerst: **Datei-Import** (CSV der deutschen Banken, CAMT.053, MT940) und manuelle Buchungen. Kein
Bank-Passwort auf dem Server. Automatischer Abruf per FinTS (python-fints, PIN/TAN) ist Stufe 6 und optional.

1. **Konten & Umsätze** (`invest/money/`): Tabellen accounts, tx, cat_rules, imports. Import mit Bank-Erkennung
   (Sparkasse, DKB, ING, comdirect, N26, Volksbank, Postbank, PayPal, sonst Spalten-Zuordnung von Hand),
   Duplikatschutz, Kategorien mit eingebauten Regeln + lernenden eigenen Regeln, Umbuchungen erkennen.
2. **Verträge & Fixkosten:** wiederkehrende Zahlungen erkennen (Gläubiger-ID/Mandat, Gegenüber, Abstand,
   Betrag), Vertragsordner, Laufzeit und Kündigungsfrist (§ 309 Nr. 9 BGB, § 11 VVG als Hinweis),
   Fristen-Wecker, Preiserhöhung erkennen, Kündigungsschreiben als Vorlage. Gehalt als wiederkehrende Einnahme.
3. **Monat, Budgets, Prognose:** Einnahmen/Ausgaben je Monat und Kategorie, Sparquote, Vergleich zum
   Durchschnitt, Budgets je Kategorie, Kontostand-Prognose bis Monatsende/nächstes Gehalt, Gesamtvermögen.
4. **Einbindung:** Überblick-Karten, Finanzprofil aus echten Zahlen, KI-Ratgeber-Werkzeuge (Umsätze, Verträge,
   Budgets) + höheres Token-Limit (Claude), Telegram-Wecker (Frist, Budget, Kontostand).
5. **Sicherheit & Zuverlässigkeit:** PIN-Pflicht für Konten/Verträge, tägliche Sicherung, Export, Tests,
   Screenshot-QA, Umstieg.
6. **Optional:** automatischer Abruf per FinTS.

Stand 22.11.2026: Plan steht, Arbeit pausiert auf Wunsch. Tabellen-Entwurf (accounts, tx, cat_rules, imports,
contracts, budgets) liegt nur in `/root/invest-tool-next/invest/db.py`, noch nicht live.

## KI-Ratgeber: Zugang (22.11.2026)
- Gemeinsamer Zugang: TLLX-Proxy `https://proxy.chat.tllx.eu/v1`, Modell `TLLX/Qwen3.8-27b`, Responses API,
  Denktiefe `medium`, max. 8.000 Token (Schlüssel wie in `~/.pi/agent/models.json`, in der DB-Tabelle `secrets`).
  Gemessen: low 22 s, medium 28 s, Server-Standard 35 s für eine Frage mit 2 Rechnern.
- Denktiefe (`reasoning.effort` / `reasoning_effort`) und Antwortlimit sind je Zugang einstellbar; lehnt ein
  Server die Denktiefe ab, wird ohne sie wiederholt.

## Ideen für später
- Kraken-WebSocket für Alarme in Sekunden statt 5 min
- Steuer genauer: Verkäufe bei Rebalancing/Entnahme, Vorabpauschale
