"""acy7 (New API 网关) 签到站。
鉴权：POST /api/user/login (账号密码) -> 取 `session` cookie；
      GET /api/user/self -> 取 user id，写入 `new-api-user` 请求头。
签到：POST /api/user/checkin -> 解析 data.quota_awarded。
Cloudflare/WAF 拦截时降级到 Playwright 浏览器内绕过。
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

        # 用浏览器级别请求头，避免被站点的 CSRF/WAF 规则拦截
        try:
            resp = s.post(
                f"{base}/api/user/login",
                json={"username": self.account.user, "password": self.account.password},
                headers=BROWSER_HEADERS,
                timeout=30,
            )
            data = resp.json()
        except json.JSONDecodeError:
            # 返回非 JSON —— 可能是 WAF/Cloudflare 拦截页
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

        # 检测 WAF/Cloudflare 拦截（部分 WAF 会返回 JSON 格式错误）
        if detect_cloudflare(resp.status_code, resp.text):
            self.logger.warning(f"acy7/{self.account.name} 登录被 WAF 拦截")
            raise CloudflareBlocked()

        # 诊断：记录登录响应的完整信息
        self.logger.info(
            f"acy7/{self.account.name} 登录响应: HTTP {resp.status_code}, "
            f"body={resp.text[:500]}, "
            f"Set-Cookie={resp.headers.get('Set-Cookie', 'N/A')}, "
            f"session_cookies={dict(s.cookies)}"
        )

        if not data.get("success"):
            # 兼容不同版本的错误字段：message / msg / error
            msg = (
                data.get("message")
                or data.get("msg")
                or data.get("error")
                or str(data)
            )
            raise RuntimeError(f"登录失败: {msg}")

        # 优先使用响应设置的 session cookie；否则尝试从 body 取 token 手动写入
        session_cookie = s.cookies.get("session")
        d = data.get("data")
        self.logger.info(
            f"acy7/{self.account.name} 登录成功, data类型={type(d).__name__}, "
            f"data值={str(d)[:200]}, session_cookie={'有' if session_cookie else '无'}"
        )
        token = None
        if isinstance(d, dict):
            token = d.get("token") or d.get("access_token")
        if not session_cookie and token:
            s.cookies.set("session", token)
            session_cookie = token
        if not session_cookie and isinstance(d, str):
            s.cookies.set("session", d)
            session_cookie = d

        # user_id 优先取 login 响应的 data.id
        user_id = None
        if isinstance(d, dict):
            user_id = d.get("id")
        if not user_id:
            try:
                r = s.get(f"{base}/api/user/self", timeout=30)
                self.logger.info(
                    f"acy7/{self.account.name} /api/user/self: "
                    f"HTTP {r.status_code}, body={r.text[:300]}"
                )
                if r.status_code == 200:
                    j = r.json()
                    if j.get("success"):
                        user_id = (j.get("data") or {}).get("id")
            except Exception as e:
                self.logger.warning(f"acy7/{self.account.name} 查询 /api/user/self 失败: {e}")
        if user_id:
            s.headers["new-api-user"] = str(user_id)
        self.logger.info(
            f"acy7/{self.account.name} 认证信息: "
            f"session_cookie={'有' if session_cookie else '无'}, "
            f"user_id={user_id}, cookies={dict(s.cookies)}, "
            f"headers={ {k:v for k,v in s.headers.items() if k.lower() in ('new-api-user','cookie','authorization')} }"
        )
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
            resp = s.post(
                f"{base}/api/user/checkin",
                headers=BROWSER_HEADERS,
                timeout=30,
            )
        except Exception as e:
            raise RuntimeError(f"签到请求失败: {e}")
        if resp.status_code == 401:
            self.logger.warning(
                f"acy7/{self.account.name} 签到返回 401, "
                f"body={resp.text[:300]}, cookies={dict(s.cookies)}, "
                f"headers={ {k:v for k,v in s.headers.items() if k.lower() in ('new-api-user','cookie','authorization')} }"
            )
            raise AuthExpired()
        try:
            data = resp.json()
        except json.JSONDecodeError:
            if detect_cloudflare(resp.status_code, resp.text):
                raise CloudflareBlocked()
            raise RuntimeError("签到返回非 JSON 且非 WAF 拦截")
        if resp.status_code == 200 and data.get("success"):
            d = data.get("data", {}) or {}
            awarded = d.get("quota_awarded") or 0
            # 查询账户总额度
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
            r = s.get(f"{base}/api/user/self", timeout=30)
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
            if cached.get("cookies"):
                try:
                    ctx.add_cookies(cached["cookies"])
                except Exception:
                    pass
            page = ctx.new_page()
            try:
                page.goto(base, wait_until="domcontentloaded", timeout=60000)
                # 等待挑战通过（通常由浏览器自动完成）
                page.wait_for_timeout(8000)
                # 确保已登录
                page.evaluate(
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
                        {"ok": True, "points": d.get("quota_awarded"), "msg": "浏览器绕过签到成功"}
                    )
                return self._result({"ok": False, "msg": data.get("message", "浏览器绕过签到失败")})
            except Exception as e:
                return self._result({"ok": False, "msg": f"浏览器绕过异常: {e}"})
            finally:
                browser.close()
