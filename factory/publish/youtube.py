"""YouTube Shorts через официальный YouTube Data API v3 (resumable upload).

Токен берётся у панели (Cloudflare Worker хранит refresh token после входа через Google)
или из секретов GitHub YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN (автономный режим).
Квота: videos.insert — отдельный лимит 100 загрузок в день, нам нужно 3.
"""
import json
import os

from ..net import Worker, request

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"


def access_token(worker: Worker):
    if worker.on:
        try:
            return worker.get("/factory/token/youtube")["access_token"]
        except Exception as e:
            print("⚠️  токен YouTube из панели не получен:", e)
    rt = os.environ.get("YT_REFRESH_TOKEN")
    if rt:
        res, _ = request("POST", "https://oauth2.googleapis.com/token", form={
            "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
            "refresh_token": rt, "grant_type": "refresh_token"})
        return res["access_token"]
    return None


def upload(path, meta, token, privacy="public", category="27"):
    size = os.path.getsize(path)
    body = {
        "snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                    "tags": meta.get("tags", [])[:15], "categoryId": category,  # 27 = Education
                    "defaultLanguage": "uk"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False, "embeddable": True},
    }
    _, hdrs = request("POST", UPLOAD_URL, data=body, headers={
        "Authorization": "Bearer " + token, "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size)})
    loc = hdrs.get("Location") or hdrs.get("location")
    with open(path, "rb") as f:
        data = f.read()
    res, _ = request("PUT", loc, raw=data, headers={"Authorization": "Bearer " + token,
                                                    "Content-Type": "video/mp4"}, timeout=600)
    if isinstance(res, (bytes, str)):
        res = json.loads(res)
    vid = res["id"]
    real_privacy = res.get("status", {}).get("privacyStatus", privacy)
    out = {"id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": real_privacy, "status": "published"}
    if privacy == "public" and real_privacy != "public":
        out["status"] = "locked_private"
        out["note"] = "YouTube сделал видео приватным: проекту API нужен аудит (см. README)."
    return out


def stats(ids, token):
    out = {}
    for i in range(0, len(ids), 50):
        chunk = ",".join(ids[i:i + 50])
        res, _ = request("GET", f"https://www.googleapis.com/youtube/v3/videos?part=statistics,status&id={chunk}",
                         headers={"Authorization": "Bearer " + token})
        for it in res.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0)),
                             "privacy": it.get("status", {}).get("privacyStatus")}
    return out
