"""Состояние фабрики в репозитории (state/*.json) — коммитится GitHub Actions после каждого запуска."""
import copy
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")


def _p(name):
    return os.path.join(STATE, name)


def load(name, default):
    try:
        with open(_p(name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(default)


def save(name, data):
    os.makedirs(STATE, exist_ok=True)
    tmp = _p(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _p(name))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(worker):
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    weights = load("stats.json", {}).get("weights", {})
    if worker.on:
        try:
            remote = worker.get("/factory/config")
            cfg = deep_merge(cfg, remote.get("settings", {}))
            weights = remote.get("weights") or weights
        except Exception as e:
            print("⚠️  настройки из панели не получены, используем config.json:", e)
    return cfg, weights


class History:
    def __init__(self):
        self.items = load("history.json", [])
        self.used = load("used.json", {"keys": [], "items": {}})

    def is_used(self, key):
        return key in set(self.used["keys"])

    def mark(self, spec):
        today = now_iso()[:10]
        self.used["keys"].append(spec["key"])
        for i in spec["items"]:
            self.used["items"][i] = today

    def add(self, entry):
        self.items = [e for e in self.items if e["id"] != entry["id"]] + [entry]
        self.items = self.items[-1500:]

    def get(self, vid):
        return next((e for e in self.items if e["id"] == vid), None)

    def recent_formats(self, n=3):
        return [e["format"] for e in self.items[-n:]]

    def save(self):
        save("history.json", self.items)
        save("used.json", self.used)
