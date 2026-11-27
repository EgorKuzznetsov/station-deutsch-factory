"""Заголовки, описания и хештеги для YouTube Shorts, TikTok и Telegram."""
from .i18n import tr

TAGS = {
    "uk": ["#німецькамова", "#німецька", "#deutschlernen", "#вивчаюнімецьку", "#німеччина", "#learngerman"],
    "ru": ["#немецкийязык", "#немецкий", "#deutschlernen", "#учимнемецкий", "#германия", "#learngerman"],
}
FMT_TAGS = {
    "translate": ["#слова", "#wortschatz"], "find_error": ["#grammatik", "#помилки"],
    "article": ["#artikel", "#derdiedas"], "level_test": ["#тест", "#deutschtest"],
    "a1_vs_b2": ["#b2", "#deutschb2"], "five_words": ["#wortschatz", "#vokabeln"],
    "phrases": ["#umgangssprache", "#розмовнанімецька"], "fill_blank": ["#grammatik", "#übung"],
    "mistakes": ["#fehler", "#типовіпомилки"], "mini_exam": ["#prüfung", "#deutschprüfung"],
}
FMT_TAGS_RU = {"phrases": ["#umgangssprache", "#разговорныйнемецкий"], "mistakes": ["#fehler", "#ошибки"],
               "find_error": ["#grammatik", "#ошибки"]}


def first(spec, t):
    for sc in spec["scenes"]:
        if sc["type"] == t:
            return sc
    return {}


def title_for(spec):
    lang, f = spec["lang"], spec["format"]
    uk = lang == "uk"
    q, c, card = first(spec, "mcq"), first(spec, "compare"), first(spec, "card")
    err = first(spec, "error")
    if f == "translate":
        word = (q.get("big_article") + " " if q.get("big_article") else "") + q.get("big", "")
        s = f"Що означає «{word}»? Німецька за 10 секунд" if uk else f"Что значит «{word}»? Немецкий за 10 секунд"
        if word.rstrip()[-1:] in "?!.":
            s = (f"Що означає: «{word}» Німецька за 10 секунд" if uk
                 else f"Что значит: «{word}» Немецкий за 10 секунд")
    elif f == "article":
        words = ", ".join(sc["big"] for sc in spec["scenes"] if sc["type"] == "mcq")
        s = ("der, die чи das? " if uk else "der, die или das? ") + words
    elif f == "find_error":
        s = (f"Знайди помилку ({spec['level']}): «{err.get('wrong', '')}»" if uk
             else f"Найди ошибку ({spec['level']}): «{err.get('wrong', '')}»")
    elif f == "level_test":
        s = "Тест: твій рівень німецької A1–B2" if uk else "Тест: твой уровень немецкого A1–B2"
    elif f == "a1_vs_b2":
        s = (f"«{c.get('top', '')}» — як сказати на B2?" if uk else f"«{c.get('top', '')}» — как сказать на B2?")
    elif f == "five_words":
        topic = spec.get("extra", {}).get("topic", "")
        s = (f"5 німецьких слів: {topic}" if uk else f"5 немецких слов: {topic}")
    elif f == "phrases":
        s = (f"Розмовна німецька: «{card.get('de', '')}» і ще 2 фрази" if uk
             else f"Разговорный немецкий: «{card.get('de', '')}» и ещё 2 фразы")
    elif f == "fill_blank":
        s = (f"Встав слово: «{q.get('big', '')}»" if uk else f"Вставь слово: «{q.get('big', '')}»")
    elif f == "mistakes":
        s = (f"«{c.get('top', '')}» — так не кажуть!" if uk else f"«{c.get('top', '')}» — так не говорят!")
    else:
        s = "Міні-іспит з німецької: 3 питання" if uk else "Мини-экзамен по немецкому: 3 вопроса"
    return s.strip()


def hashtags(spec, n=8):
    lang = spec["lang"] if spec["lang"] in TAGS else "uk"
    ft = (FMT_TAGS_RU if lang == "ru" else {}).get(spec["format"]) or FMT_TAGS.get(spec["format"], [])
    lv = ["#deutsch" + spec.get("level", "A1").lower()]
    return (TAGS[lang][:4] + ft + lv + TAGS[lang][4:])[:n]


def build(spec, settings):
    t = tr(spec["lang"])
    handle = settings.get("botHandle", "station_deutsch_bot").lstrip("@")
    link = settings.get("botUrl") or f"https://t.me/{handle}"
    title = title_for(spec)
    tags = hashtags(spec)
    yt_title = (title[:88] + " #shorts") if len(title) > 88 else title + " #shorts"
    learn = spec["format"] in ("five_words", "phrases", "a1_vs_b2", "mistakes")
    ans = t["desc_save"] if learn else t["desc_answer"]
    yt_desc = (f"{title}\n\n{ans}\n\n{t['desc_cta']}\n👉 Telegram: {link}\n\n" + " ".join(tags))
    tt_caption = (f"{title} 🇩🇪\n{ans}\n"
                  f"{'Більше — у Telegram' if spec['lang'] == 'uk' else 'Больше — в Telegram'}: @{handle}\n"
                  + " ".join(tags))
    tg_caption = f"🎬 {title}\n\nTikTok:\n{tt_caption}"
    return {"title": title, "youtube": {"title": yt_title, "description": yt_desc,
                                        "tags": [x.lstrip("#") for x in tags]},
            "tiktok": {"caption": tt_caption[:2200]}, "telegram": {"caption": tg_caption[:1024]},
            "hashtags": tags}
