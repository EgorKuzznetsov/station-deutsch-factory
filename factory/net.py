"""Мини-HTTP-клиент на стандартной библиотеке (без лишних зависимостей)."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class HttpError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status, self.body = status, body


def request(method, url, data=None, headers=None, form=None, raw=None, timeout=120, retries=2):
    headers = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json; charset=UTF-8")
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif raw is not None:
        body = raw
    headers.setdefault("User-Agent", "station-deutsch-factory/1.0")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read()
                ctype = r.headers.get("Content-Type", "")
                hdrs = dict(r.headers)
                if "json" in ctype:
                    return json.loads(txt or b"{}"), hdrs
                return txt, hdrs
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code >= 500 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise HttpError(e.code, msg)
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def get_json(url, headers=None):
    return request("GET", url, headers=headers)[0]


def post_json(url, data, headers=None):
    return request("POST", url, data=data, headers=headers)[0]


class Worker:
    """Клиент панели управления (Cloudflare Worker). Если FACTORY_URL не задан — работает офлайн."""

    def __init__(self):
        self.url = os.environ.get("FACTORY_URL", "").rstrip("/")
        self.secret = os.environ.get("FACTORY_SECRET", "")

    @property
    def on(self):
        return bool(self.url and self.secret)

    def _h(self):
        return {"Authorization": "Bearer " + self.secret}

    def get(self, path):
        return get_json(self.url + path, self._h())

    def post(self, path, data):
        return post_json(self.url + path, data, self._h())

    def safe_post(self, path, data):
        if not self.on:
            return None
        try:
            return self.post(path, data)
        except Exception as e:  # панель не должна ломать производство видео
            print("⚠️  панель недоступна:", e)
            return None
