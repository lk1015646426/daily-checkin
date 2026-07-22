"""Token/Cookie 本地缓存：跨运行持久化登录态（GitHub Actions 由 actions/cache 持久化 store/ 目录）。"""
import json
import os
import time


class TokenStore:
    def __init__(self, path="store/tokens.json"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, site, account):
        return self._load().get(f"{site}:{account}")

    def save(self, site, account, value):
        data = self._load()
        value = dict(value)
        value["updated_at"] = int(time.time())
        if "expires_at" not in value:
            value["expires_at"] = None
        data[f"{site}:{account}"] = value
        self._save(data)

    def is_expired(self, value):
        exp = value.get("expires_at")
        if not exp:
            return False
        return time.time() > exp
