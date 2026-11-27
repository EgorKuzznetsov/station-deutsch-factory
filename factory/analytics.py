"""Ежедневная аналитика: собирает просмотры/лайки/комментарии и пересчитывает веса форматов.

Логика — простой «многорукий бандит»:
  • счёт ролика = просмотры + 10×лайки + 20×комментарии + 20×репосты (YouTube + TikTok);
  • для каждого формата берём медиану счёта за последние 30 дней;
  • вес = медиана формата / средняя медиана, ограничен 0.35…3.0;
  • форматы с < 3 роликами получают бонус 1.3 — система продолжает их тестировать.
"""
import statistics
from datetime import datetime, timedelta, timezone

from . import state
from .generators import FORMATS
from .net import Worker
from .publish import tiktok, youtube


def score(e):
    s = 0
    for p in ("youtube", "tiktok"):
        st = (e.get(p) or {}).get("stats") or {}
        s += st.get("views", 0) + 10 * st.get("likes", 0) + 20 * st.get("comments", 0) + 20 * st.get("shares", 0)
    return s


def run():
    w = Worker()
    hist = state.History()
    since = datetime.now(timezone.utc) - timedelta(days=45)
    recent = [e for e in hist.items if datetime.strptime(e["created"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc) >= since]

    yt_ids = [e["youtube"]["id"] for e in recent if (e.get("youtube") or {}).get("id")]
    if yt_ids:
        tok = youtube.access_token(w)
        if tok:
            try:
                st = youtube.stats(yt_ids, tok)
                for e in recent:
                    y = e.get("youtube") or {}
                    if y.get("id") in st:
                        y["stats"] = st[y["id"]]
            except Exception as ex:
                print("⚠️  YouTube stats:", ex)

    tt_ids = [e["tiktok"]["post_id"] for e in recent if (e.get("tiktok") or {}).get("post_id")]
    if tt_ids:
        tok = tiktok.access_token(w)
        if tok:
            try:
                st = tiktok.stats(tt_ids, tok)
                for e in recent:
                    t = e.get("tiktok") or {}
                    if t.get("post_id") in st:
                        t["stats"] = st[t["post_id"]]
            except Exception as ex:
                print("⚠️  TikTok stats (нужен scope video.list):", ex)

    last30 = datetime.now(timezone.utc) - timedelta(days=30)
    per = {f: [] for f in FORMATS}
    for e in hist.items:
        created = datetime.strptime(e["created"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if created >= last30 and e.get("format") in per and created < datetime.now(timezone.utc) - timedelta(hours=20):
            per[e["format"]].append(score(e))
    med = {f: statistics.median(v) for f, v in per.items() if len(v) >= 3}
    avg = statistics.mean(med.values()) if med else 0
    weights = {}
    for f in FORMATS:
        if f in med and avg > 0:
            weights[f] = round(min(3.0, max(0.35, med[f] / avg)), 3)
        else:
            weights[f] = 1.3
    totals = {
        "videos": len(hist.items),
        "views": sum(((e.get("youtube") or {}).get("stats") or {}).get("views", 0)
                     + ((e.get("tiktok") or {}).get("stats") or {}).get("views", 0) for e in hist.items),
    }
    by_format = {f: {"count": len(per[f]), "median_score": med.get(f), "weight": weights[f]} for f in FORMATS}
    stats = {"updated": state.now_iso(), "weights": weights, "byFormat": by_format, "totals": totals}
    state.save("stats.json", stats)
    hist.save()
    w.safe_post("/factory/stats", stats)
    w.safe_post("/factory/history/bulk", {"items": hist.items[-300:]})
    print("📊 веса форматов:", weights)
    return stats
