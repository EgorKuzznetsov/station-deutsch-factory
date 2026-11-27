"""Загрузка банка заданий: встроенные проверенные данные + (опционально) пополнение из content/generated/."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(HERE, "content")
GENERATED = os.path.join(CONTENT, "generated")


def _load_json(name):
    out = []
    for folder in (CONTENT, GENERATED):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.extend(json.load(f))
    return out


def load_nouns():
    nouns, seen = [], set()
    with open(os.path.join(CONTENT, "nouns.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            art, word, uk, ru, level, topic = line.split("|")
            if word in seen:
                continue
            seen.add(word)
            nouns.append({"id": "n:" + word, "art": art, "de": word, "uk": uk, "ru": ru,
                          "level": level, "topic": topic})
    return nouns


class Bank:
    def __init__(self):
        self.nouns = load_nouns()
        self.phrases = _load_json("phrases.json")
        self.fill = _load_json("fill.json")
        self.errors = _load_json("errors.json")
        self.mistakes = _load_json("mistakes.json")
        self.a1b2 = _load_json("a1b2.json")
        for name in ("phrases", "fill", "errors", "mistakes", "a1b2"):
            items = getattr(self, name)
            # уникальность id
            ids = [i["id"] for i in items]
            dup = {x for x in ids if ids.count(x) > 1}
            if dup:
                raise ValueError(f"Дубли id в {name}: {dup}")

    def stats(self):
        return {k: len(getattr(self, k)) for k in ("nouns", "phrases", "fill", "errors", "mistakes", "a1b2")}
