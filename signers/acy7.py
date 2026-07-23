"""acy7 (New API 网关) 签到站。

鉴权：POST /api/user/login (账号密码) -> 取 `session` cookie；
      GET /api/user/self -> 取 user id，写入 `new-api-user` 请求头。
签到：POST /api/user/checkin -> 解析 data.quota_awarded。
Cloudflare 拦截时降级到 Playwright 浏览器内绕过。
"""
import json

from common.session import detect_cloudflare
from .base import AuthExpired, BaseSigner, CloudflareBlocked


class Acy7Signer(BaseSigner):
    type = "acy7"

    def login(self):
        base = self.site.base_url.rstrip("/")
        s = self.session
        s.cookies.clear()
        try:
            resp = s.post(
                f"{base}/api/user/login",
                json={"username": self.account.user, "password": self.account.password},
                timeout=30,
            )
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"登录请求失败: {e}")

        if not data.get("success"):
            raise RuntimeError(f"登录失败: {data.get('message')}")

        # 优先使用响应设置的 session cookie；否则尝试从 body 取 token 手动写入
        session_cookie = s.cookies.get("session")
        d = data.get("data")
        token = None
        if isinstance(d, dict):
            token = d.get("token") or d.get("access_token")
        if not session_cookie and token:
            s.cookies.set("session", token)
            session_cookie = token
        if not session_cookie and isinstance(d, str):
            s.cookies.set("session", d)
            session_cookie = d

        # user_id 优先取 login 响应的 data.id：/api/user/self 需要 new-api-user 头，
        # 未设置时返回 401，而 login 响应本就带 id，直接用可避免"要头才能取 id、
        # 要 id 才能设头"的死循环。仅当 login 无 id 时才回退调 /api/user/self。
        user_id = None
        if isinstance(d, dict):
            user_id = d.get("id")
        if not user_id:
            try:
                r = s.get(f"{base}/api/user/self", timeout=30)
                if r.status_code == 200:
                    j = r.json()
                    if j.get("success"):
                        user_id = (j.get("data") or {}).get("id")
            except Exception:
                pass
        if user_id:
            s.headers["new-api-user"] = str(user_id)

        return {
            "token": session_cookie,
            "user_id": user_id,
            "cookies": dict(s.cookies),
            "expires_at": None,
        }

    def checkin(self, auth):
        base = self.site.base_url.rstrip("/")
        s = self.session
        try:
            resp = s.post(f"{base}/api/user/checkin", timeout=30)
        except Exception as e:
            raise RuntimeError(f"签到请求失败: {e}")

        if resp.status_code == 401:
            raise AuthExpired()
        try:
            data = resp.json()
        except json.JSONDecodeError:
            if detect_cloudflare(resp.status_code, resp.text):
                raise CloudflareBlocked()
            raise RuntimeError("签到返回非 JSON 且非 Cloudflare 拦截")

        if resp.status_code == 200 and data.get("success"):
            d = data.get("data", {}) or {}
            return {
                "ok": True,
                "points": d.get("quota_awarded"),
                "msg": data.get("message", "签到成功"),
            }

        msg = data.get("message", "") or ""
        if "已签到" in msg or "already" in msg.lower() or "今日" in msg:
            return {"ok": True, "points": 0, "msg": msg or "今日已签到"}
        return {"ok": False, "points": 0, "msg": msg or "签到失败"}

    def _cf_bypass(self):
        """Cloudflare 拦截时，用 Playwright 在浏览器内完成挑战并签到。"""
        from playwright.sync_api import sync_playwright

        base = self.site.base_url.rstrip("/")
        cached = self.store.get(self.site_name, self.account.name) or {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if cached.get("cookies"):
                try:
                    ctx.add_cookies(cached["cookies"])
                except Exception:
                    pass
            page = ctx.new_page()
            try:
                page.goto(base, wait_until="domcontentloaded", timeout=60000)
                # 等待 CF 挑战通过（通常由浏览器自动完成）
                page.wait_for_timeout(8000)
                # 确保已登录
                page.evaluate(
                    """async (u, pw) => {
                        const r = await fetch('/api/user/login', {
                            method:'POST', headers:{'Content-Type':'application/json'},
                            body: JSON.stringify({username:u, password:pw})
                        });
                        return await r.text();
                    }""",
                    self.account.user,
                    self.account.password,
                )
                page.wait_for_timeout(2000)
                result = page.evaluate(
                    """async () => {
                        const r = await fetch('/api/user/checkin', {method:'POST'});
                        return await r.text();
                    }"""
                )
                data = json.loads(result)
                cookies = ctx.cookies()
                self.store.save(
                    self.site_name,
                    self.account.name,
                    {"cookies": cookies, "user_id": cached.get("user_id"), "expires_at": None},
                )
                if data.get("success"):
                    d = data.get("data", {}) or {}
                    return self._result(
                        {"ok": True, "points": d.get("quota_awarded"), "msg": "CF绕过签到成功"}
                    )
                return self._result({"ok": False, "msg": data.get("message", "CF绕过签到失败")})
            except Exception as e:
                return self._result({"ok": False, "msg": f"CF绕过异常: {e}"})
            finally:
                browser.close()
