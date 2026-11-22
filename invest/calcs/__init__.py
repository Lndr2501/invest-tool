"""Rechner-Baukasten.

Ein Rechner = eine Funktion mit ``@calculator(...)``. Sie bekommt (con, profile, inputs) und liefert ein Ergebnis-Dict:

    {"tiles":  [{"label", "value", "note"?, "tone"?: "good"|"bad"|"accent"}],
     "text":   ["Satz", …],
     "charts": [{"type": "line"|"stack"|"bar"|"hbar", "title", "x": [...], "series": [{"name", "data", "role"}],
                 "unit": "money"|"pct"|"num", "note"?}],
     "table":  {"head": [...], "rows": [[...], …]},
     "notes":  ["Annahme …"],
     "actions": [{"label", "type": "save_finance"|"link", "data"|"href"}]}

Felder (``fields``) beschreiben das Formular – die Website baut es daraus, die KI liest sie als Parameter-Schema:
    {"key", "label", "type": money|number|percent|years|select|toggle|market, "default", "min", "max", "step",
     "help", "options": [[wert, text], …], "show_if": {"key": wert}}

Neue Rechner: Datei in diesem Ordner anlegen und unten in ``_MODULES`` eintragen.
"""
from __future__ import annotations

import importlib
from typing import Callable

REGISTRY: dict[str, dict] = {}
CATEGORIES = [
    ("build", "Vermögen aufbauen"),
    ("past", "Rückblick"),
    ("plan", "Planen"),
    ("cost", "Kosten & Steuern"),
    ("learn", "Verstehen"),
]
_MODULES = ["sims", "basics", "planning", "taxes"]


class CalcError(ValueError):
    pass


def calculator(cid: str, title: str, question: str, category: str, fields: list[dict], *, ui: str = "generic",
               icon: str = "", order: int = 50):
    def deco(fn: Callable):
        REGISTRY[cid] = {"id": cid, "title": title, "question": question, "category": category, "fields": fields,
                         "ui": ui, "icon": icon, "order": order, "fn": fn}
        return fn
    return deco


def _load() -> None:
    if REGISTRY:
        return
    for m in _MODULES:
        importlib.import_module(f"{__name__}.{m}")


def catalog(profile: dict | None = None) -> list[dict]:
    _load()
    out = []
    for c in sorted(REGISTRY.values(), key=lambda c: ([k for k, _ in CATEGORIES].index(c["category"]), c["order"])):
        fields = c["fields"](profile) if callable(c["fields"]) else c["fields"]
        out.append({k: v for k, v in c.items() if k not in ("fn", "fields")} | {"fields": fields})
    return out


def defaults(cid: str, profile: dict | None = None) -> dict:
    _load()
    c = REGISTRY[cid]
    fields = c["fields"](profile) if callable(c["fields"]) else c["fields"]
    return {f["key"]: f.get("default") for f in fields}


def run(con, profile: dict, cid: str, inputs: dict) -> dict:
    _load()
    if cid not in REGISTRY:
        raise CalcError("Unbekannter Rechner.")
    c = REGISTRY[cid]
    fields = c["fields"](profile) if callable(c["fields"]) else c["fields"]
    vals = {}
    for f in fields:
        v = inputs.get(f["key"], f.get("default"))
        t = f["type"]
        if t in ("money", "number", "percent", "years"):
            try:
                v = float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                raise CalcError(f"„{f['label']}“ muss eine Zahl sein.") from None
            if v is not None:
                if "min" in f and v < f["min"]:
                    v = f["min"]
                if "max" in f and v > f["max"]:
                    v = f["max"]
        elif t == "toggle":
            v = v in (True, 1, "1", "true", "ja", "on")
        elif t == "select":
            opts = [o[0] for o in f.get("options", [])]
            if v not in opts:
                v = f.get("default")
        vals[f["key"]] = v
    # Rechner mit eigener Oberfläche bekommen zusätzlich alle übrigen Eingaben (z. B. Gewichte)
    extra = {k: v for k, v in inputs.items() if k not in vals}
    res = c["fn"](con, profile, {**extra, **vals})
    res.setdefault("id", cid)
    res.setdefault("title", c["title"])
    return res


# ---------- kleine Helfer für Rechner ----------
def cur_of(profile: dict | None) -> str:
    return (profile or {}).get("settings", {}).get("currency", "EUR")


def money_field(key, label, default, help_="", **kw):
    return {"key": key, "label": label, "type": "money", "default": default, "min": 0, "help": help_, **kw}


def pct_field(key, label, default, help_="", lo=-20.0, hi=50.0, step=0.1, **kw):
    return {"key": key, "label": label, "type": "percent", "default": default, "min": lo, "max": hi, "step": step,
            "help": help_, **kw}


def years_field(key, label, default, help_="", lo=1, hi=60, **kw):
    return {"key": key, "label": label, "type": "years", "default": default, "min": lo, "max": hi, "step": 1,
            "help": help_, **kw}
