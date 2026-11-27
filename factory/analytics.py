"""Аналитика: каждые 3 часа собирает статистику YouTube и TikTok ОТДЕЛЬНО и считает:

  • итоги по каждой площадке (ролики, просмотры, лайки, комментарии);
  • прирост просмотров по дням (последние 21 день) для каждой площадки;
  • среднее по времени выхода: 12:00, 18:00 и ручные запуски;
  • эффективность форматов отдельно для YouTube и TikTok;
  • веса форматов для автооптимизации (что заходит лучше — выходит чаще).

TikTok: берём список ВСЕХ роликов аккаунта (scope video.list), поэтому статистика собирается и для
роликов, которые ты опубликовал вручную из черновика. Ролик связывается с заданием по заголовку.
"""
import statistics
from datetime import datetime, timedelta, timezone

from . import state
from .generators import FORMATS
from .net import Worker
from .publish import tiktok, youtube

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    BERLIN = timezone(timedelta(hours=1))

PLATFORMS = ("youtube", "tiktok")


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def slot_of(e):
    h = parse(e["created"]).astimezone(BERLIN).hour
    if 11 <= h <= 13:
        return "12:00"
    if 17 <= h <= 19:
        return "18:00"
    return "manual"


def norm(t):
    return "".join(ch.lower() for ch in (t or "") if ch.isalnum())


def st(e, p):
    return ((e.get(p) or {}).get("stats")) or None


def collect_youtube(w, items):
    ids = [e["youtube"]["id"] for e in items if (e.get("youtube") or {}).get("id")]
    if not ids:
        return
    tok = youtube.access_token(w)
    if not tok:
        return
    try:
        data = youtube.stats(ids, tok)
    except Exception as ex:
        print("⚠️  YouTube stats:", ex)
        return
    for e in items:
        y = e.get("youtube") or {}
        if y.get("id") in data:
            y["stats"] = data[y["id"]]


def collect_tiktok(w, items):
    """Возвращает список всех роликов TikTok-аккаунта (для итогов) или None."""
    tok = tiktok.access_token(w)
    if not tok:
        return None
    try:
        videos = tiktok.list_videos(tok)
    except Exception as ex:
        print("⚠️  TikTok stats (нужен scope video.list и переподключение TikTok в панели):", ex)
        return None
    by_id = {str(v["id"]): v for v in videos}
    used = set()
    for e in items:  # уже известные post_id
        t = e.get("tiktok") or {}
        if t.get("post_id") and str(t["post_id"]) in by_id:
            used.add(str(t["post_id"]))
    for e in sorted(items, key=lambda x: x["created"]):  # связываем вручную опубликованные по заголовку
        t = e.get("tiktok")
        if not t or t.get("post_id") or t.get("status") in ("off", "not_connected", None):
            continue
        key = norm(e.get("title", ""))[:30]
        if not key:
            continue
        created = parse(e["created"])
        for v in videos:
            vid = str(v["id"])
            text = norm((v.get("title") or "") + " " + (v.get("video_description") or ""))
            when = datetime.fromtimestamp(v.get("create_time", 0), timezone.utc)
            if vid not in used and key in text and when >= created - timedelta(minutes=5):
                t["post_id"], t["status"] = vid, "published"
                used.add(vid)
                break
    for e in items:
        t = e.get("tiktok") or {}
        v = by_id.get(str(t.get("post_id") or ""))
        if v:
            t["stats"] = {"views": v.get("view_count", 0), "likes": v.get("like_count", 0),
                          "comments": v.get("comment_count", 0), "shares": v.get("share_count", 0)}
    return videos


def score(s):
    return s.get("views", 0) + 10 * s.get("likes", 0) + 20 * s.get("comments", 0) + 20 * s.get("shares", 0)


def agg(entries, p):
    vals = [st(e, p) for e in entries if st(e, p)]
    views = [v.get("views", 0) for v in vals]
    return {"count": len(vals), "views": sum(views), "avg": round(statistics.mean(views)) if views else 0,
            "likes": sum(v.get("likes", 0) for v in vals), "comments": sum(v.get("comments", 0) for v in vals)}


def run():
    w = Worker()
    hist = state.History()
    now = datetime.now(timezone.utc)
    recent = [e for e in hist.items if parse(e["created"]) >= now - timedelta(days=60)]

    collect_youtube(w, recent)
    tt_videos = collect_tiktok(w, recent)

    # --- итоги по площадкам
    platforms = {"youtube": agg(hist.items, "youtube"), "tiktok": agg(hist.items, "tiktok")}
    if tt_videos is not None:  # итоги TikTok по всему аккаунту (включая ролики не от фабрики)
        platforms["tiktok"].update({
            "accountVideos": len(tt_videos),
            "accountViews": sum(v.get("view_count", 0) for v in tt_videos)})
    platforms["tiktok"]["connected"] = tt_videos is not None

    # --- по дням: снимок общего числа просмотров → прирост за день
    daily = state.load("daily.json", {})
    today = now.astimezone(BERLIN).strftime("%Y-%m-%d")
    daily[today] = {"youtube": platforms["youtube"]["views"],
                    "tiktok": platforms["tiktok"].get("accountViews", platforms["tiktok"]["views"])}
    days = sorted(daily)[-60:]
    daily = {d: daily[d] for d in days}
    state.save("daily.json", daily)
    series, prev = [], None
    for d in days:
        cur = daily[d]
        gain = {p: max(0, cur[p] - (prev[p] if prev else 0)) for p in PLATFORMS}
        published = {p: sum(1 for e in hist.items if (e.get(p) or {}).get("status") == "published"
                            and parse(e["created"]).astimezone(BERLIN).strftime("%Y-%m-%d") == d)
                     for p in PLATFORMS}
        series.append({"date": d, "views": gain, "videos": published})
        prev = cur

    # --- по времени выхода и по форматам (отдельно для каждой площадки)
    slots = {s: {p: agg([e for e in hist.items if slot_of(e) == s], p) for p in PLATFORMS}
             for s in ("12:00", "18:00", "manual")}
    by_format = {f: {p: agg([e for e in hist.items if e.get("format") == f], p) for p in PLATFORMS}
                 for f in FORMATS}

    # --- веса: средний «счёт» формата за 30 дней (ролики старше 20 часов, обе площадки вместе)
    mature = [e for e in hist.items if now - timedelta(days=30) <= parse(e["created"]) <= now - timedelta(hours=20)]
    per = {f: [] for f in FORMATS}
    for e in mature:
        if e.get("format") in per:
            per[e["format"]].append(sum(score(st(e, p)) for p in PLATFORMS if st(e, p)))
    med = {f: statistics.median(v) for f, v in per.items() if len(v) >= 3}
    avg = statistics.mean(med.values()) if med else 0
    weights = {f: (round(min(3.0, max(0.35, med[f] / avg)), 3) if f in med and avg > 0 else 1.3) for f in FORMATS}
    for f in FORMATS:
        by_format[f]["weight"] = weights[f]
        by_format[f]["mature"] = len(per[f])

    stats = {"updated": state.now_iso(), "weights": weights, "platforms": platforms, "daily": series[-21:],
             "slots": slots, "byFormat": by_format,
             "totals": {"videos": len(hist.items),
                        "views": platforms["youtube"]["views"] + platforms["tiktok"]["views"]}}
    state.save("stats.json", stats)
    hist.save()
    w.safe_post("/factory/stats", stats)
    w.safe_post("/factory/history/bulk", {"items": hist.items[-300:]})
    print("📊 YouTube:", platforms["youtube"], "\n📊 TikTok:", platforms["tiktok"], "\n⚖️  веса:", weights)
    return stats
