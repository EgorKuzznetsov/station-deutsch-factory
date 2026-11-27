"""TikTok через официальный Content Posting API.

Режимы (настраиваются в панели):
  direct — прямая публикация (Direct Post). Пока приложение не прошло аудит TikTok,
           аккаунт должен быть приватным, а ролики публикуются только как SELF_ONLY.
  inbox  — загрузка черновика во «Входящие» TikTok: приходит уведомление, ты жмёшь «Опубликовать».
  off    — не публиковать в TikTok (ролик всё равно придёт тебе в Telegram).

Токены TikTok хранит и обновляет панель (Cloudflare Worker): access token живёт 24 часа,
refresh token — 365 дней.
"""
import os
import time

from ..net import Worker, request

API = "https://open.tiktokapis.com/v2"


def access_token(worker: Worker):
    if worker.on:
        try:
            return worker.get("/factory/token/tiktok")["access_token"]
        except Exception as e:
            print("⚠️  токен TikTok из панели не получен:", e)
    return os.environ.get("TT_ACCESS_TOKEN")


def _h(token):
    return {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=UTF-8"}


def _check(res):
    err = res.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok: {err.get('code')} — {err.get('message')}")
    return res.get("data", {})


def creator_info(token):
    res, _ = request("POST", API + "/post/publish/creator_info/query/", data={}, headers=_h(token))
    return _check(res)


def _upload(upload_url, path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read()
    request("PUT", upload_url, raw=data, headers={"Content-Type": "video/mp4", "Content-Length": str(size),
                                                  "Content-Range": f"bytes 0-{size - 1}/{size}"}, timeout=600)


def _source(path):
    size = os.path.getsize(path)  # наши ролики < 64 МБ → один чанк
    return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}


def post(path, caption, token, cfg, duration):
    mode = cfg.get("mode", "inbox")
    if mode == "inbox":
        res, _ = request("POST", API + "/post/publish/inbox/video/init/", data={"source_info": _source(path)},
                         headers=_h(token))
        d = _check(res)
        _upload(d["upload_url"], path)
        out = {"publish_id": d["publish_id"], "mode": "inbox", "status": "uploaded"}
        out.update(wait_status(token, d["publish_id"], tries=15))  # реальный статус обработки у TikTok
        print("TikTok inbox status:", out)
        return out

    info = creator_info(token)
    maxd = info.get("max_video_post_duration_sec") or 600
    if duration > maxd:
        raise RuntimeError(f"Видео {duration:.0f}с длиннее лимита аккаунта {maxd}с")
    options = info.get("privacy_level_options", ["SELF_ONLY"])
    want = cfg.get("privacy", "PUBLIC_TO_EVERYONE")
    privacy = want if want in options else ("SELF_ONLY" if "SELF_ONLY" in options else options[0])
    body = {
        "post_info": {
            "title": caption,
            "privacy_level": privacy,
            "disable_comment": bool(cfg.get("disableComment", False) or info.get("comment_disabled")),
            "disable_duet": bool(cfg.get("disableDuet", False) or info.get("duet_disabled")),
            "disable_stitch": bool(cfg.get("disableStitch", False) or info.get("stitch_disabled")),
            "video_cover_timestamp_ms": 2500,
            # Ролик продвигает твой собственный продукт → честная пометка «Your brand»
            "brand_organic_toggle": bool(cfg.get("brandOrganic", True)),
            "brand_content_toggle": False,
            "is_aigc": False,
        },
        "source_info": _source(path),
    }
    res, _ = request("POST", API + "/post/publish/video/init/", data=body, headers=_h(token))
    d = _check(res)
    _upload(d["upload_url"], path)
    out = {"publish_id": d["publish_id"], "mode": "direct", "privacy": privacy, "status": "processing",
           "creator": info.get("creator_nickname")}
    out.update(wait_status(token, d["publish_id"]))
    return out


def wait_status(token, publish_id, tries=20):
    for _ in range(tries):
        time.sleep(6)
        try:
            res, _ = request("POST", API + "/post/publish/status/fetch/", data={"publish_id": publish_id},
                             headers=_h(token))
            d = _check(res)
        except Exception as e:
            return {"status": "unknown", "note": str(e)[:200]}
        st = d.get("status")
        if st == "PUBLISH_COMPLETE":
            ids = d.get("publicaly_available_post_id") or []
            return {"status": "published", "post_id": str(ids[0]) if ids else None}
        if st == "FAILED":
            return {"status": "failed", "note": d.get("fail_reason")}
        if st == "SEND_TO_USER_INBOX":
            return {"status": "sent_to_inbox"}
    return {"status": "processing"}


def stats(ids, token):
    out = {}
    for i in range(0, len(ids), 20):
        res, _ = request("POST", API + "/video/query/?fields=id,view_count,like_count,comment_count,share_count",
                         data={"filters": {"video_ids": ids[i:i + 20]}}, headers=_h(token))
        for v in _check(res).get("videos", []):
            out[str(v["id"])] = {"views": v.get("view_count", 0), "likes": v.get("like_count", 0),
                                 "comments": v.get("comment_count", 0), "shares": v.get("share_count", 0)}
    return out
