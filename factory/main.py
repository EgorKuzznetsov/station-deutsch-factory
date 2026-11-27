"""Точка входа фабрики.

  python -m factory.main produce [--count 1] [--format article] [--no-publish]
  python -m factory.main publish --id <video_id> --platform tiktok      # публикация после одобрения
  python -m factory.main analytics
  python -m factory.main validate [--no-lt]                              # проверка всего банка заданий
  python -m factory.main preview --format article [--theme mint]         # видео без публикации
"""
import argparse
import json
import os
import random
import sys
import traceback

from . import analytics, meta, state
from .bank import Bank
from .checker import Checker
from .generators import FORMATS, Gen, choose_format
from .net import Worker
from .publish import telegram, tiktok, youtube
from .render import render
from .themes import get_theme

OUT = os.path.join(state.ROOT, "out")


def summary(text):
    print(text)
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def make_spec(bank, hist, settings, weights, checker, rng, fmt=None):
    tried = []
    for _ in range(30):
        f = fmt or choose_format(settings, weights, hist.recent_formats(2), rng)
        spec = Gen(bank, hist.used, settings, rng).build(f)
        if not spec or hist.is_used(spec["key"]):
            tried.append(f + ": нет уникальных заданий")
            continue
        errs = checker.check(spec)
        if errs:
            tried.append(f + ": " + "; ".join(errs))
            continue
        return spec
    raise RuntimeError("Не удалось собрать уникальное проверенное задание:\n" + "\n".join(tried[-6:]))


def publish_youtube(entry, path, m, settings, worker):
    tok = youtube.access_token(worker)
    if not tok:
        return {"status": "not_connected"}
    return youtube.upload(path, m["youtube"], tok, settings.get("youtube", {}).get("privacy", "public"))


def publish_tiktok(entry, path, m, settings, worker, duration, force=False):
    cfg = settings.get("tiktok", {})
    if cfg.get("mode") == "off":
        return {"status": "off"}
    if cfg.get("requireApproval") and not force:
        return {"status": "awaiting_approval", "mode": cfg.get("mode", "inbox")}
    tok = tiktok.access_token(worker)
    if not tok:
        return {"status": "not_connected"}
    return tiktok.post(path, m["tiktok"]["caption"], tok, cfg, duration)


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "note": str(e)[:400]}


def produce(count=1, fmt=None, no_publish=False, theme=None, force=False, record=True):
    worker = Worker()
    settings, weights = state.load_settings(worker)
    if not settings.get("enabled", True) and not force:
        summary("⏸ Фабрика выключена в настройках.")
        return []
    os.makedirs(OUT, exist_ok=True)
    hist, bank = state.History(), Bank()
    checker = Checker(state.STATE, use_lt=os.environ.get("NO_LT") != "1")
    rng = random.Random()
    results = []
    for _ in range(count):
        try:
            spec = make_spec(bank, hist, settings, weights, checker, rng, fmt)
        except Exception as e:
            summary(f"❌ {e}")
            telegram.send_text(f"⚠️ Фабрика: {e}")
            continue
        tname, th = get_theme(theme or settings.get("theme", "rotate"), rng)
        mp4 = os.path.join(OUT, spec["id"] + ".mp4")
        cover = os.path.join(OUT, spec["id"] + ".jpg")
        duration = render(spec, th, mp4, brand="Station Deutsch", cover_path=cover, sound=settings.get("sound"))
        m = meta.build(spec, settings)
        entry = {"id": spec["id"], "created": state.now_iso(), "format": spec["format"], "level": spec["level"],
                 "lang": spec["lang"], "theme": tname, "duration": round(duration, 1), "title": m["title"],
                 "key": spec["key"], "runId": os.environ.get("GITHUB_RUN_ID"),
                 "youtube": None, "tiktok": None, "telegram": None}
        if record:
            hist.mark(spec)  # отмечаем сразу — даже при сбое публикации задание не повторится
        pub = settings.get("publish", {})
        if not no_publish:
            if pub.get("youtube", True):
                entry["youtube"] = safe(publish_youtube, entry, mp4, m, settings, worker)
            if pub.get("tiktok", True):
                entry["tiktok"] = safe(publish_tiktok, entry, mp4, m, settings, worker, duration)
        if pub.get("telegram", True):
            cap = m["telegram"]["caption"]
            tts = (entry.get("tiktok") or {}).get("status")
            if tts == "awaiting_approval":
                cap = "⏳ Ждёт одобрения в панели (TikTok)\n" + cap
            elif tts in ("sent_to_inbox", "uploaded", "processing"):
                cap = ("🔔 TikTok: черновик уже во «Входящих» → Systembenachrichtigungen → "
                       "«Dein Inhalt von Station Factory ist bereit» → Posten.\n"
                       "Подпись с хештегами ниже, её можно скопировать.\n\n") + cap
            entry["telegram"] = safe(telegram.send_video, mp4, cap, cover)
        if record:
            hist.add(entry)
            hist.save()
        checker.save()
        with open(os.path.join(OUT, spec["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump({"spec": {k: v for k, v in spec.items() if k != "itemsData"}, "meta": m, "entry": entry},
                      f, ensure_ascii=False, indent=1)
        if record:
            worker.safe_post("/factory/history", entry)
        yt, tt = entry.get("youtube") or {}, entry.get("tiktok") or {}
        summary(f"✅ {spec['format']} · {spec['level']} · {duration:.1f}s · «{m['title']}»\n"
                f"   YouTube: {yt.get('status', '—')} {yt.get('url', '')}  ·  TikTok: {tt.get('status', '—')}")
        for name, r in (("YouTube", yt), ("TikTok", tt)):
            if r.get("status") in ("error", "failed", "locked_private"):
                telegram.send_text(f"⚠️ {name}: {r.get('status')}: {r.get('note', '')}\n{m['title']}")
        results.append(entry)
    return results


def publish_one(vid, platform):
    worker = Worker()
    settings, _ = state.load_settings(worker)
    hist = state.History()
    path = os.path.join(OUT, vid + ".mp4")
    with open(os.path.join(OUT, vid + ".json"), encoding="utf-8") as f:
        data = json.load(f)
    entry = hist.get(vid) or data["entry"]
    if platform == "tiktok":
        entry["tiktok"] = safe(publish_tiktok, entry, path, data["meta"], settings, worker, entry["duration"], True)
    elif platform == "youtube":
        entry["youtube"] = safe(publish_youtube, entry, path, data["meta"], settings, worker)
    hist.add(entry)
    hist.save()
    worker.safe_post("/factory/history", entry)
    summary(f"{platform}: {(entry.get(platform) or {}).get('status')}")


def validate(use_lt=True):
    bank = Bank()
    ch = Checker(state.STATE, use_lt=use_lt)
    errs = []
    for n in bank.nouns:
        if n["art"] not in ("der", "die", "das"):
            errs.append(f"{n['id']}: артикль {n['art']}")
    for f in bank.fill:
        if f["s"].count("___") != 1:
            errs.append(f"{f['id']}: пропусков != 1")
        if len({o.lower() for o in f["o"]}) != len(f["o"]):
            errs.append(f"{f['id']}: варианты повторяются")
    for e in bank.errors:
        if e["bad"] not in e["wrong"] or (e["fix"] and e["fix"] not in e["right"]) or e["wrong"] == e["right"]:
            errs.append(f"{e['id']}: несогласованные wrong/right/bad/fix")
    texts = ([(f["id"], f["s"].replace("___", f["o"][0])) for f in bank.fill]
             + [(e["id"], e["right"]) for e in bank.errors]
             + [(m["id"], m["right"].replace("…", "").strip()) for m in bank.mistakes]
             + [(c["id"], c["a1"]) for c in bank.a1b2] + [(c["id"], c["b2"]) for c in bank.a1b2]
             + [(p["id"], p["de"].replace("...", "").strip()) for p in bank.phrases])
    if use_lt:
        for i, t in texts:
            if ch.lt(t) is False:
                errs.append(f"{i}: LanguageTool — «{t}»")
        ch.save()
    summary(f"Банк: {bank.stats()}\nПроверено текстов: {len(texts)}\nПроблем: {len(errs)}")
    for e in errs:
        summary("  • " + e)
    return errs


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("produce")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--format", choices=FORMATS)
    p.add_argument("--theme")
    p.add_argument("--no-publish", action="store_true")
    p.add_argument("--force", action="store_true", help="запуск даже если фабрика выключена")
    p = sub.add_parser("publish")
    p.add_argument("--id", required=True)
    p.add_argument("--platform", default="tiktok", choices=["tiktok", "youtube"])
    sub.add_parser("analytics")
    p = sub.add_parser("validate")
    p.add_argument("--no-lt", action="store_true")
    p = sub.add_parser("preview")
    p.add_argument("--format", choices=FORMATS)
    p.add_argument("--theme")
    a = ap.parse_args()
    if a.cmd == "produce":
        produce(a.count, a.format, a.no_publish, a.theme, a.force)
    elif a.cmd == "publish":
        publish_one(a.id, a.platform)
    elif a.cmd == "analytics":
        analytics.run()
    elif a.cmd == "validate":
        sys.exit(1 if validate(not a.no_lt) else 0)
    elif a.cmd == "preview":
        os.environ.setdefault("NO_LT", "1")
        produce(1, a.format, no_publish=True, theme=a.theme, force=True, record=False)


if __name__ == "__main__":
    main()
