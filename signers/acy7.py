"""acy7 (New API 网关) 签到站。
鉴权：POST /api/user/login (账号密码) -> 取 data.access_token；
      后续请求用 Authorization: Bearer <access_token> 头认证。
      GET /api/user/self -> 取 user id，写入 `new-api-user` 请求头。
签到：POST /api/user/checkin -> 解析 data.quota_awarded。
WAF/Cloudflare 拦截时降级到 Playwright 浏览器内绕过。
"""
import json
from common.session import detect_cloudflare
from .base import AuthExpired, BaseSigner, CloudflareBlocked

# 浏览器级别请求头：New API 站点可能检查 Origin/Referer 做 CSRF 防护
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://acy7.com",
    "Referer": "https://acy7.com/",
}


class Acy7Signer(BaseSigner):
    type = "acy7"

    def login(self):
        base = self.site.base_url.rstrip("/")
        s = self.session
        s.cookies.clear()
        # 清除可能残留的旧认证头
        s.headers.pop("Authorization", None)
        s.headers.pop("new-api-user", None)

        try:
            resp = s.post(
                f"{base}/api/user/login",
                json={"username": self.account.user, "password": self.account.password},
                headers=BROWSER_HEADERS,
                timeout=30,
            )
            data = resp.json()
        except json.JSONDecodeError:
            self.logger.error(
                f"acy7/{self.account.name} 登录返回非 JSON: "
                f"HTTP {resp.status_code}, body={resp.text[:500]}"
            )
            if detect_cloudflare(resp.status_code, resp.text):
                raise CloudflareBlocked()
            raise RuntimeError(
                f"登录返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        except Exception as e:
            raise RuntimeError(f"登录请求失败: {e}")

        if detect_cloudflare(resp.status_code, resp.text):
            raise CloudflareBlocked()

        if not data.get("success"):
            msg = (
                data.get("message")
                or data.get("msg")
                or data.get("error")
                or str(data)
            )
            raise RuntimeError(f"登录失败: {msg}")

        # 新版 New API：登录成功后返回 access_token，用 Bearer 认证
        d = data.get("data")
        access_token = None
        if isinstance(d, dict):
            access_token = d.get("access_token") or d.get("token")

        # 兼容旧版：如果没有 access_token，回退到 session cookie
        if not access_token:
            session_cookie = s.cookies.get("session")
            if session_cookie:
                access_token = session_cookie
            elif isinstance(d, str):
                access_token = d

        if not access_token:
            raise RuntimeError(
                f"登录成功但未获取到 access_token, 响应: {resp.text[:300]}"
            )

        # 设置 Bearer token 认证头
        s.headers["Authorization"] = f"Bearer {access_token}"

        # 获取 user_id（新版需要 Bearer token 才能访问 /api/user/self）
        user_id = None
        if isinstance(d, dict):
            user_id = d.get("id")
        if not user_id:
            try:
                r = s.get(f"{base}/api/user/self", headers=BROWSER_HEADERS, timeout=30)
                if r.status_code == 200:
                    j = r.json()
                    if j.get("success"):
                        user_id = (j.get("data") or {}).get("id")
            except Exception as e:
                self.logger.warning(f"acy7/{self.account.name} 查询 user_id 失败: {e}")
        if user_id:
            s.headers["new-api-user"] = str(user_id)

        self.logger.info(
            f"acy7/{self.account.name} 登录成功, "
            f"access_token={'有' if access_token else '无'}, "
            f"user_id={user_id}"
        )
        return {
            "token": access_token,
            "user_id": user_id,
            "cookies": {},
            "expires_at": None,
        }

    def apply_auth(self, auth):
        """从缓存恢复 Bearer token 认证。"""
        token = auth.get("token")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        uid = auth.get("user_id")
        if uid:
            self.session.headers["new-api-user"] = str(uid)

    def checkin(self, auth):
        base = self.site.base_url.rstrip("/")
        s = self.session
        try:
            resp = s.post(
                f"{base}/api/user/checkin",
                headers=BROWSER_HEADERS,
                timeout=30,
            )
        except Exception as e:
            raise RuntimeError(f"签到请求失败: {e}")
        if resp.status_code == 401:
            raise AuthExpired()
        try:
            data = resp.json()
        except json.JSONDecodeError:
            if detect_cloudflare(resp.status_code, resp.text):
                raise CloudflareBlocked()
            raise RuntimeError(f"签到返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}")
        if resp.status_code == 200 and data.get("success"):
            d = data.get("data", {}) or {}
            awarded = d.get("quota_awarded") or 0
            total_quota = self._get_quota()
            total_usd = round(total_quota / 500000, 2) if total_quota else 0
            awarded_usd = round(awarded / 500000, 2) if awarded else 0
            return {
                "ok": True,
                "points": total_usd,
                "points_unit": "USD",
                "awarded": awarded_usd,
                "msg": f"签到 +${awarded_usd:.2f}，总额度 ${total_usd:.2f}",
            }
        msg = data.get("message", "") or ""
        if "已签到" in msg or "already" in msg.lower() or "今日" in msg:
            total_quota = self._get_quota()
            total_usd = round(total_quota / 500000, 2) if total_quota else 0
            return {
                "ok": True,
                "points": total_usd,
                "points_unit": "USD",
                "awarded": 0,
                "msg": f"今日已签到，总额度 ${total_usd:.2f}",
            }
        return {"ok": False, "points": 0, "msg": msg or "签到失败"}

    def _get_quota(self):
        """查询账户当前总额度（quota）。"""
        base = self.site.base_url.rstrip("/")
        s = self.session
        try:
            r = s.get(f"{base}/api/user/self", headers=BROWSER_HEADERS, timeout=30)
            if r.status_code == 200:
                j = r.json()
                if j.get("success"):
                    return (j.get("data") or {}).get("quota", 0) or 0
        except Exception as e:
            self.logger.warning(f"acy7/{self.account.name} 查询额度失败: {e}")
        return 0

    def _cf_bypass(self):
        """WAF/Cloudflare 拦截时，用 Playwright 在浏览器内完成挑战并签到。"""
        from playwright.sync_api import sync_playwright

        base = self.site.base_url.rstrip("/")
        cached = self.store.get(self.site_name, self.account.name) or {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                page.goto(base, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                # 浏览器内登录并签到
                login_result = page.evaluate(
                    """async (u, pw) => {
                        const r = await fetch('/api/user/login', {
                            method:'POST',
                            headers:{
                                'Content-Type':'application/json',
                                'Origin': window.location.origin,
                                'Referer': window.location.origin + '/',
                            },
                            body: JSON.stringify({username:u, password:pw})
                        });
                        const data = await r.json();
                        if (data.success && data.data && data.data.access_token) {
                            // 用 access_token 设置后续请求的认证头
                            window.__token = data.data.access_token;
                        }
                        return JSON.stringify(data);
                    }""",
                    self.account.user,
                    self.account.password,
                )
                page.wait_for_timeout(2000)
                result = page.evaluate(
                    """async () => {
                        const headers = {};
                        if (window.__token) {
                            headers['Authorization'] = 'Bearer ' + window.__token;
                        }
                        const r = await fetch('/api/user/checkin', {
                            method:'POST',
                            headers: headers
                        });
                        return await r.text();
                    }"""
                )
                data = json.loads(result)
                if data.get("success"):
                    d = data.get("data", {}) or {}
                    awarded = d.get("quota_awarded") or 0
                    awarded_usd = round(awarded / 500000, 2) if awarded else 0
                    return self._result(
                        {"ok": True, "points": awarded_usd, "points_unit": "USD",
                         "msg": f"浏览器签到成功 +${awarded_usd:.2f}"}
                    )
                return self._result({"ok": False, "msg": data.get("message", "浏览器签到失败")})
            except Exception as e:
                return self._result({"ok": False, "msg": f"浏览器绕过异常: {e}"})
            finally:
                browser.close()
