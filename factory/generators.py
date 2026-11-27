"""Генераторы 10 форматов. Каждый возвращает «сценарий» (spec): список сцен + использованные задания."""
import hashlib
import random
from datetime import date, datetime

from .i18n import tr

LEVELS = ["A1", "A2", "B1", "B2"]
FORMATS = ["translate", "find_error", "article", "level_test", "a1_vs_b2",
           "five_words", "phrases", "fill_blank", "mistakes", "mini_exam"]


def base(text):
    """Нормализация перевода для сравнения дистракторов: «рахунок (банк)» → «рахунок»."""
    return text.split(" (")[0].strip().lower()


class Picker:
    """Выбор заданий без повторов: учитывает «остывание» (cooldown) каждого задания."""

    def __init__(self, used, rng, levels, cooldown_days=60):
        self.items = used.get("items", {})
        self.rng = rng
        self.levels = set(levels or LEVELS)
        self.cooldown = cooldown_days
        self.today = date.today()
        self.taken = set()

    def age(self, item_id):
        d = self.items.get(item_id)
        if not d:
            return 10 ** 6
        return (self.today - datetime.strptime(d[:10], "%Y-%m-%d").date()).days

    def pick(self, pool, n=1, levels=None, key=None):
        lv = set(levels) if levels else self.levels
        cands = [x for x in pool if x.get("level", "A1") in lv and x["id"] not in self.taken]
        if key:
            cands = [x for x in cands if key(x)]
        if len(cands) < n:
            return []
        fresh = [x for x in cands if self.age(x["id"]) >= self.cooldown]
        if len(fresh) >= n:
            chosen = self.rng.sample(fresh, n)
        else:  # свежие закончились → берём задания, которые не показывались дольше всего
            self.rng.shuffle(cands)
            cands.sort(key=lambda x: -self.age(x["id"]))
            chosen = cands[:n]
        for c in chosen:
            self.taken.add(c["id"])
        return chosen


def _mcq(prompt, big, options, answer, think, reveal, explain="", style="text", counter="", big_article=None, sub=""):
    return {"type": "mcq", "prompt": prompt, "big": big, "options": options, "answer": answer,
            "think": think, "reveal": reveal, "dur": think + reveal, "explain": explain,
            "style": style, "counter": counter, "big_article": big_article, "sub": sub}


def _shuffle_opts(rng, correct, wrong):
    opts = [correct] + list(wrong)
    rng.shuffle(opts)
    return opts, opts.index(correct)


class Gen:
    def __init__(self, bank, used, settings, rng=None):
        self.b = bank
        self.s = settings
        self.lang = settings.get("uiLang", "uk")
        self.t = tr(self.lang)
        self.rng = rng or random.Random()
        self.p = Picker(used, self.rng, settings.get("levels"), settings.get("cooldownDays", 60))
        self.think = float(settings.get("thinkSeconds", 5))

    # ---------- элементы ----------
    def noun_question(self, think, reveal, counter="", levels=None):
        n = self.p.pick(self.b.nouns, 1, levels=levels)
        if not n:
            return None, []
        n = n[0]
        same = [x for x in self.b.nouns if x["id"] != n["id"] and base(x[self.lang]) != base(n[self.lang])]
        topical = [x for x in same if x["topic"] == n["topic"]]
        pool = topical if len(topical) >= 2 else same
        wrong = []
        for x in self.rng.sample(pool, len(pool)):
            if all(base(x[self.lang]) != base(w) for w in wrong):
                wrong.append(x[self.lang])
            if len(wrong) == 2:
                break
        opts, ans = _shuffle_opts(self.rng, n[self.lang], wrong)
        sc = _mcq(self.t["q"]["translate"], n["de"], opts, ans, think, reveal,
                  explain=f'{n["art"]} {n["de"]} = {n[self.lang]}', counter=counter, big_article=n["art"])
        return sc, [n]

    def phrase_question(self, think, reveal, counter=""):
        ph = self.p.pick(self.b.phrases, 1)
        if not ph:
            return None, []
        ph = ph[0]
        pool = [x for x in self.b.phrases if x["id"] != ph["id"] and base(x[self.lang]) != base(ph[self.lang])]
        wrong = [x[self.lang] for x in self.rng.sample(pool, 2)]
        opts, ans = _shuffle_opts(self.rng, ph[self.lang], wrong)
        return _mcq(self.t["q"]["translate_phrase"], ph["de"], opts, ans, think, reveal, counter=counter), [ph]

    def article_question(self, think, reveal, counter="", levels=None):
        n = self.p.pick(self.b.nouns, 1, levels=levels)
        if not n:
            return None, []
        n = n[0]
        opts = ["der", "die", "das"]
        return _mcq(self.t["q"]["article"], n["de"], opts, opts.index(n["art"]), think, reveal,
                    explain=f'{n["art"]} {n["de"]}', style="article", counter=counter, sub=n[self.lang]), [n]

    def fill_question(self, think, reveal, counter="", levels=None):
        f = self.p.pick(self.b.fill, 1, levels=levels)
        if not f:
            return None, []
        f = f[0]
        opts, ans = _shuffle_opts(self.rng, f["o"][0], f["o"][1:])
        return _mcq(self.t["q"]["fill_blank"], f["s"], opts, ans, think, reveal, explain=f["x"],
                    style="blank", counter=counter), [f]

    # ---------- форматы ----------
    def hook(self, fmt, extra=None):
        title = self.rng.choice(self.t["hooks"][fmt])
        if "{topic}" in title:
            title = title.format(topic=extra or "")
        return {"type": "hook", "title": title, "badge": self.t["fmt"][fmt], "dur": 1.7}

    def cta(self):
        c = self.s.get("cta", {})
        return {"type": "cta", "dur": 3.2,
                "line1": c.get("line1") or self.t["cta1"],
                "handle": "@" + self.s.get("botHandle", "station_deutsch_bot").lstrip("@"),
                "line3": c.get("line3") if c.get("line3") is not None else self.t["cta3"]}

    def build(self, fmt):
        fn = getattr(self, "f_" + fmt)
        res = fn()
        if not res:
            return None
        scenes, items, level, extra = res
        hook_extra = extra.get("topic") if extra else None
        for sc in scenes:
            if sc["type"] in ("mcq", "error"):
                sc["hint"] = self.t["q"]["write_comments"]
        scenes = [self.hook(fmt, hook_extra)] + scenes + [self.cta()]
        ids = sorted(i["id"] for i in items)
        key = fmt + ":" + ",".join(ids)
        vid = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(key.encode()).hexdigest()[:6]
        return {"id": vid, "format": fmt, "level": level, "lang": self.lang, "key": key, "items": ids,
                "scenes": scenes, "extra": extra or {}, "itemsData": items}

    def f_translate(self):
        if self.rng.random() < 0.55:
            sc, it = self.noun_question(self.think, 2.6)
        else:
            sc, it = self.phrase_question(self.think, 2.6)
        if not sc:
            return None
        return [sc], it, it[0]["level"], {}

    def f_article(self):
        scenes, items = [], []
        for i in range(3):
            sc, it = self.article_question(3.0, 1.6, counter=f"{i + 1}/3")
            if not sc:
                return None
            scenes.append(sc)
            items += it
        return scenes, items, _max_level(items), {}

    def f_fill_blank(self):
        sc, it = self.fill_question(self.think, 2.8)
        if not sc:
            return None
        return [sc], it, it[0]["level"], {}

    def f_find_error(self):
        e = self.p.pick(self.b.errors, 1)
        if not e:
            return None
        e = e[0]
        sc = {"type": "error", "prompt": self.t["q"]["find_error"], "wrong": e["wrong"], "right": e["right"],
              "bad": e["bad"], "fix": e["fix"], "explain": e["x"], "think": self.think, "reveal": 3.4,
              "dur": self.think + 3.4}
        return [sc], [e], e["level"], {}

    def f_level_test(self):
        scenes, items = [], []
        allowed = [lv for lv in LEVELS if lv in self.p.levels] or LEVELS
        for lv in allowed[:4]:
            sc, it = self.fill_question(3.5, 1.8, counter=lv, levels=[lv])
            if not sc:
                return None
            scenes.append(sc)
            items += it
        scenes.append({"type": "result", "title": self.t["result"]["level_test"],
                       "lines": self.t["result"]["scale"][:len(scenes)], "dur": 2.4})
        return scenes, items, allowed[-1], {}

    def f_mini_exam(self):
        scenes, items = [], []
        makers = [self.fill_question, self.noun_question, self.article_question]
        self.rng.shuffle(makers)
        for i, mk in enumerate(makers):
            sc, it = mk(4.0, 1.8, counter=f"{i + 1}/3")
            if not sc:
                return None
            scenes.append(sc)
            items += it
        scenes.append({"type": "result", "title": self.t["result"]["mini_exam"],
                       "lines": self.t["result"]["exam_scale"], "dur": 2.2})
        return scenes, items, _max_level(items), {}

    def f_a1_vs_b2(self):
        pairs = self.p.pick(self.b.a1b2, 2, levels=["A1", "A2", "B1", "B2", None])
        if not pairs:
            return None
        scenes = [{"type": "compare", "mode": "level", "top": p["a1"], "bottom": p["b2"],
                   "top_label": "A1", "bottom_label": "B2", "note": p[self.lang], "dur": 4.4,
                   "counter": f"{i + 1}/2"} for i, p in enumerate(pairs)]
        return scenes, pairs, "B2", {}

    def f_mistakes(self):
        ms = self.p.pick(self.b.mistakes, 3)
        if not ms:
            return None
        scenes = [{"type": "compare", "mode": "fix", "top": m["wrong"], "bottom": m["right"],
                   "top_label": self.t["q"]["wrong"], "bottom_label": self.t["q"]["right"],
                   "note": m[self.lang], "dur": 3.9, "counter": f"{i + 1}/3"} for i, m in enumerate(ms)]
        return scenes, ms, _max_level(ms), {}

    def f_five_words(self):
        topics = {}
        for n in self.b.nouns:
            if n["level"] in self.p.levels:
                topics.setdefault(n["topic"], []).append(n)
        good = [t for t, lst in topics.items()
                if len([x for x in lst if self.p.age(x["id"]) >= self.p.cooldown]) >= 5]
        if not good:
            good = [t for t, lst in topics.items() if len(lst) >= 5]
        if not good:
            return None
        topic = self.rng.choice(good)
        words = self.p.pick(topics[topic], 5)
        if not words:
            return None
        scenes = [{"type": "card", "article": w["art"], "de": w["de"], "tr": w[self.lang],
                   "counter": f"{i + 1}/5", "dur": 2.3} for i, w in enumerate(words)]
        tname = self.t["topics"].get(topic, topic)
        return scenes, words, _max_level(words), {"topic": tname}

    def f_phrases(self):
        ps = self.p.pick(self.b.phrases, 3)
        if not ps:
            return None
        scenes = [{"type": "card", "article": None, "de": p["de"], "tr": p[self.lang],
                   "counter": f"{i + 1}/3", "dur": 3.3} for i, p in enumerate(ps)]
        return scenes, ps, _max_level(ps), {}


def _max_level(items):
    lv = [i.get("level") for i in items if i.get("level") in LEVELS]
    return max(lv, key=LEVELS.index) if lv else "A1"


def choose_format(settings, weights, recent, rng):
    """Взвешенный выбор формата: веса пользователя × веса аналитики, без повтора подряд."""
    cfg = settings.get("formats", {})
    w = {}
    for f in FORMATS:
        c = cfg.get(f, {})
        if c.get("enabled", True) is False:
            continue
        base_w = float(c.get("weight", 1.0))
        auto = float(weights.get(f, 1.0)) if settings.get("autoOptimize", True) else 1.0
        w[f] = max(0.0, base_w * auto)
    for f in recent or []:  # не повторяем форматы последних роликов
        if len(w) > 1:
            w.pop(f, None)
    total = sum(w.values())
    if total <= 0:
        return rng.choice(FORMATS)
    r = rng.random() * total
    for f, v in w.items():
        r -= v
        if r <= 0:
            return f
    return list(w)[-1]
