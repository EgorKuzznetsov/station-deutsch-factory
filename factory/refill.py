"""(Опционально) Пополнение банка заданий через бесплатный Gemini API.

Работает, только если в GitHub Secrets есть GEMINI_API_KEY (бесплатный ключ из Google AI Studio).
Каждое новое задание проходит те же проверки, что и встроенные: структура + LanguageTool.
Сомнительные задания отбрасываются. Принятые — сохраняются в factory/content/generated/*.json
с пометкой "src": "ai" и попадают в ролики только после проверки.

  python -m factory.refill --kind fill --n 15
"""
import argparse
import json
import os
import re

from . import state
from .bank import GENERATED, Bank
from .checker import Checker
from .net import request

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

PROMPTS = {
    "fill": ('Create {n} German gap-fill exercises for Ukrainian learners at levels A1-B2 about work, '
             'Ausbildung, offices, daily life in Germany. JSON array of objects: '
             '{{"level":"A2","s":"sentence with exactly one ___ gap","o":["correct","wrong1","wrong2"],'
             '"x":"short rule in German grammar terms, max 40 chars"}}. Only ONE option may be grammatically '
             'correct. Avoid regional variants and ambiguity.'),
    "errors": ('Create {n} German sentences with exactly one typical learner mistake (A1-B2). JSON array: '
               '{{"level":"B1","wrong":"...","right":"...","bad":"exact wrong fragment from wrong",'
               '"fix":"exact corrected fragment from right","x":"short rule, max 40 chars"}}. '
               'The right sentence must be fully correct standard German.'),
    "mistakes": ('Create {n} typical mistakes Ukrainians make in German. JSON array: '
                 '{{"level":"A2","wrong":"...","right":"...","uk":"short explanation in Ukrainian",'
                 '"ru":"short explanation in Russian"}}.'),
    "phrases": ('Create {n} common colloquial German phrases used in everyday life and at work. JSON array: '
                '{{"level":"B1","de":"...","uk":"Ukrainian meaning","ru":"Russian meaning"}}. '
                'Max 5 words each, no vulgar language.'),
    "a1b2": ('Create {n} pairs: the same idea in simple A1 German and elegant B2 German, workplace/daily life. '
             'JSON array: {{"a1":"...","b2":"...","uk":"meaning in Ukrainian","ru":"meaning in Russian"}}.'),
}
FILES = {"fill": "fill.json", "errors": "errors.json", "mistakes": "mistakes.json", "phrases": "phrases.json",
         "a1b2": "a1b2.json"}


def ask_gemini(prompt):
    key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    res, _ = request("POST", url, data={"contents": [{"parts": [{"text": prompt}]}],
                                       "generationConfig": {"temperature": 0.9,
                                                            "responseMimeType": "application/json"}})
    txt = res["candidates"][0]["content"]["parts"][0]["text"]
    txt = re.sub(r"^```(json)?|```$", "", txt.strip())
    return json.loads(txt)


def valid(kind, it):
    try:
        if kind == "fill":
            return (it["s"].count("___") == 1 and len(it["o"]) == 3
                    and len({o.lower() for o in it["o"]}) == 3, it["s"].replace("___", it["o"][0]))
        if kind == "errors":
            return (it["bad"] in it["wrong"] and it["fix"] in it["right"] and it["wrong"] != it["right"],
                    it["right"])
        if kind == "mistakes":
            return (it["wrong"] != it["right"] and it["uk"] and it["ru"], it["right"])
        if kind == "phrases":
            return (len(it["de"].split()) <= 6 and it["uk"] and it["ru"], it["de"])
        if kind == "a1b2":
            return (it["a1"] != it["b2"], it["a1"] + " " + it["b2"])
    except (KeyError, TypeError):
        pass
    return False, ""


def run(kind, n):
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY не задан — пополнение пропущено (это нормально, банк и так большой).")
        return 0
    bank = Bank()
    existing = {json.dumps(x, ensure_ascii=False) for x in getattr(bank, kind)}
    known_texts = {str(x.get("s") or x.get("wrong") or x.get("de") or x.get("a1")) for x in getattr(bank, kind)}
    ch = Checker(state.STATE, use_lt=True)
    items = ask_gemini(PROMPTS[kind].format(n=n))
    os.makedirs(GENERATED, exist_ok=True)
    path = os.path.join(GENERATED, FILES[kind])
    saved = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    added = 0
    for it in items:
        ok, text = valid(kind, it)
        main_text = str(it.get("s") or it.get("wrong") or it.get("de") or it.get("a1"))
        if not ok or main_text in known_texts or json.dumps(it, ensure_ascii=False) in existing:
            continue
        if ch.lt(text) is not True:  # None (сервис недоступен) тоже не принимаем
            continue
        it["id"] = f"ai-{kind}-{len(saved) + 1:04d}"
        it["src"] = "ai"
        it.setdefault("level", "B1")
        saved.append(it)
        known_texts.add(main_text)
        added += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=1)
    ch.save()
    print(f"{kind}: добавлено {added} из {len(items)}")
    return added


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=list(PROMPTS), default=None)
    ap.add_argument("--n", type=int, default=12)
    a = ap.parse_args()
    for k in ([a.kind] if a.kind else list(PROMPTS)):
        try:
            run(k, a.n)
        except Exception as e:
            print(f"⚠️  {k}: {e}")
