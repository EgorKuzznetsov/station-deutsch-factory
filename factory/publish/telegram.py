"""Копия каждого ролика тебе в Telegram (Bot API, бесплатно).

Удобно с iPhone: видео + готовая подпись с хештегами. Если TikTok ещё не одобрил автопубликацию,
можно за 10 секунд выложить ролик вручную из Telegram.
"""
import json
import os
import uuid

from ..net import request


def send_video(path, caption, cover=None):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        return None
    boundary = uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    def file(name, fpath, ctype):
        with open(fpath, "rb") as f:
            data = f.read()
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{os.path.basename(fpath)}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")

    field("chat_id", chat)
    field("caption", caption[:1024])
    field("supports_streaming", "true")
    field("width", "1080")
    field("height", "1920")
    file("video", path, "video/mp4")
    if cover and os.path.exists(cover):
        file("thumbnail", cover, "image/jpeg")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    res, _ = request("POST", f"https://api.telegram.org/bot{token}/sendVideo", raw=body,
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=300)
    return {"ok": bool(res.get("ok")) if isinstance(res, dict) else False}


def send_text(text):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        return
    try:
        request("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})
    except Exception as e:
        print("⚠️  Telegram:", e)
