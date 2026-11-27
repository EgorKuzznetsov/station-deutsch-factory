"""Проверка правильности заданий.

1) Структурные проверки (всегда): правильный ответ есть среди вариантов, варианты различны,
   пропуск ровно один, ошибка действительно есть в предложении и т. д.
2) Грамматика через LanguageTool (бесплатный публичный API): все ПРАВИЛЬНЫЕ немецкие
   предложения должны пройти без грамматических/орфографических замечаний.
   Результаты кэшируются в state/verified.json — каждый текст проверяется один раз.
"""
import hashlib
import json
import os
import time

from .net import request

LT_URL = os.environ.get("LANGUAGETOOL_URL", "https://api.languagetool.org/v2/check")
BAD_TYPES = {"misspelling", "grammar"}
# Правила, которые дают ложные срабатывания на коротких разговорных фразах
IGNORE_RULES = {"UPPERCASE_SENTENCE_START", "PUNCTUATION_PARAGRAPH_END", "WHITESPACE_RULE",
                "DE_SENTENCE_WHITESPACE", "COMMA_PARENTHESIS_WHITESPACE", "UNPAIRED_BRACKETS"}


def h(text):
    return hashlib.sha1(text.encode()).hexdigest()[:16]


class Checker:
    def __init__(self, state_dir, use_lt=True):
        self.path = os.path.join(state_dir, "verified.json")
        self.cache = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.use_lt = use_lt
        self.lt_ok = True
        self._last = 0.0

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=0, sort_keys=True)

    # ---------- LanguageTool ----------
    def lt(self, text):
        """True — текст чистый, False — найдены ошибки, None — сервис недоступен."""
        key = h(text)
        if key in self.cache:
            return self.cache[key]["ok"]
        if not self.use_lt or not self.lt_ok:
            return None
        wait = 3.2 - (time.time() - self._last)  # лимит бесплатного API: ~20 запросов в минуту
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        try:
            res, _ = request("POST", LT_URL, form={"text": text, "language": "de-DE"}, timeout=30, retries=1)
        except Exception as e:
            print("⚠️  LanguageTool недоступен, используем только структурные проверки:", e)
            self.lt_ok = False
            return None
        issues = [m for m in res.get("matches", [])
                  if m.get("rule", {}).get("issueType") in BAD_TYPES
                  and m.get("rule", {}).get("id") not in IGNORE_RULES]
        ok = not issues
        self.cache[key] = {"ok": ok, "t": text[:80],
                           "msg": [i.get("message", "")[:120] for i in issues][:3]}
        if not ok:
            print(f"❌ LanguageTool: «{text}» →", self.cache[key]["msg"])
        return ok

    # ---------- структурные проверки ----------
    @staticmethod
    def structural(spec):
        errs = []
        for sc in spec["scenes"]:
            t = sc["type"]
            if t == "mcq":
                o = sc["options"]
                if len(o) != len({x.strip().lower() for x in o}):
                    errs.append("варианты повторяются: %s" % o)
                if not (0 <= sc["answer"] < len(o)):
                    errs.append("ответ вне диапазона")
                if sc["style"] == "blank" and sc["big"].count("___") != 1:
                    errs.append("в предложении должен быть ровно один пропуск")
                if sc["style"] == "article" and o[sc["answer"]] not in ("der", "die", "das"):
                    errs.append("неверный артикль")
            elif t == "error":
                if sc["wrong"] == sc["right"]:
                    errs.append("ошибочное и правильное предложения совпадают")
                if sc["bad"] not in sc["wrong"]:
                    errs.append("ошибочный фрагмент не найден в предложении")
                if sc["fix"] and sc["fix"] not in sc["right"]:
                    errs.append("исправление не найдено в правильном предложении")
            elif t == "compare" and sc["top"] == sc["bottom"]:
                errs.append("сравнение: тексты совпадают")
        dur = sum(s["dur"] for s in spec["scenes"])
        if not 10 <= dur <= 30:
            errs.append(f"длительность {dur:.1f}с вне 10–30с")
        return errs

    @staticmethod
    def correct_texts(spec):
        """Немецкие тексты, которые показываются как ПРАВИЛЬНЫЕ — их проверяем грамматически."""
        out = []
        for sc in spec["scenes"]:
            t = sc["type"]
            if t == "mcq" and sc["style"] == "blank":
                out.append(sc["big"].replace("___", sc["options"][sc["answer"]]))
            elif t == "mcq" and sc["style"] == "text" and sc.get("big_article") is None:
                out.append(sc["big"])  # разговорная фраза
            elif t == "error":
                out.append(sc["right"])
            elif t == "compare":
                out.append(sc["bottom"])
                if sc["mode"] == "level":
                    out.append(sc["top"])
            elif t == "card" and not sc.get("article"):
                out.append(sc["de"])
        return [x.replace("…", "").strip() for x in out if x]

    def check(self, spec):
        errs = self.structural(spec)
        for txt in self.correct_texts(spec):
            if self.lt(txt) is False:
                errs.append("грамматика: " + txt)
        return errs
