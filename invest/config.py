"""Zentrale Konfiguration. Überschreibbar per /root/.config/invest-tool/settings.toml."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = Path(os.environ.get("INVEST_DB", DATA / "invest.sqlite"))
CONF_DIR = Path(os.environ.get("INVEST_CONF", "/root/.config/invest-tool"))

# Alle Märkte: wird von invest/markets.py befüllt (Katalog + von Nutzern hinzugefügte Märkte).
# Einträge: name, kind, unit, daily=(quelle, symbol), intraday=[(quelle, symbol, währung), …], spot, tax, …
ASSETS: dict[str, dict] = {}

DEFAULTS: dict = {
    "inflation": {"geo": "DE"},
    # Kosten-Annahmen pro Asset (überschreibt die Voreinstellung je Art aus markets.KINDS)
    "costs": {
        "BTC": {"buy_fee": 0.005, "spread": 0.005, "custody_pa": 0.0},
        "GOLD": {"buy_fee": 0.0025, "spread": 0.005, "custody_pa": 0.003},
        "SILVER": {"buy_fee": 0.0025, "spread": 0.01, "custody_pa": 0.002},
        "MSCIW": {"buy_fee": 0.001, "spread": 0.002, "custody_pa": 0.002},
    },
    # Alarm-Schwellen in Prozent
    "alerts": {
        "cooldown_hours": 3,
        "zscore_1h": 4.0,
        "daily_report_hour": 8,  # Ortszeit Europe/Berlin, -1 = aus
        "BTC": {"move_1h": 3.0, "move_24h": 7.0},
        "GOLD": {"move_1h": 1.5, "move_24h": 3.0},
        "SILVER": {"move_1h": 2.5, "move_24h": 5.0},
        "MSCIW": {"move_1h": 1.5, "move_24h": 3.0},
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def settings() -> dict:
    path = CONF_DIR / "settings.toml"
    if path.exists():
        with path.open("rb") as fh:
            return _merge(DEFAULTS, tomllib.load(fh))
    return copy.deepcopy(DEFAULTS)


def telegram_conf() -> dict:
    """Liest TELEGRAM_TOKEN und TELEGRAM_CHAT_ID aus telegram.env (nie ins Log schreiben)."""
    path = CONF_DIR / "telegram.env"
    conf: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip().strip('"')
    return conf
