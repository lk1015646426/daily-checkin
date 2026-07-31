"""签到器抽象基类：定义 login/checkin 接口与统一的 run 编排（缓存复用 + 失效重登）。"""
from abc import ABC, abstractmethod


class AuthExpired(Exception):
    """登录态失效，需要重新登录。"""


class CloudflareBlocked(Exception):
    """被 Cloudflare/WAF 拦截。"""


class BaseSigner(ABC):
    type = None

    def __init__(self, site, account, session, store, logger, notifier):
        self.site = site  # common.config.Site
        self.site_name = site.key
        self.account = account  # common.config.Account
        self.session = session
        self.store = store
        self.logger = logger
        self.notifier = notifier

    @abstractmethod
    def login(self) -> dict:
        """返回鉴权数据，如 {"token":..., "user_id":..., "cookies":{...}, "expires_at":...}"""
        raise NotImplementedError

    @abstractmethod
    def checkin(self, auth: dict) -> dict:
        """返回 {"ok":bool, "points":int|None, "msg":str}"""
        raise NotImplementedError

    def apply_auth(self, auth):
        """将缓存的登录态应用到请求会话（子类可重写，如 Playwright 场景）。"""
        if auth.get("cookies"):
            self.session.cookies.update(auth["cookies"])
        uid = auth.get("user_id")
        if uid:
            self.session.headers["new-api-user"] = str(uid)

    def _result(self, res, cached=False):
        return {
            "site": self.site_name,
            "account": self.account.name,
            "ok": res.get("ok", False),
            "points": res.get("points"),
            "points_unit": res.get("points_unit", ""),
            "awarded": res.get("awarded"),
            "streak": res.get("streak"),
            "msg": res.get("msg", ""),
            "cached": cached,
        }

    def run(self):
        auth = self.store.get(self.site_name, self.account.name)
        if auth and not self.store.is_expired(auth):
            self.apply_auth(auth)
            try:
                res = self.checkin(auth)
                if res.get("ok"):
                    return self._result(res, cached=True)
            except AuthExpired:
                self.logger.info(f"{self.site_name}/{self.account.name} 登录态失效，重新登录")
            except CloudflareBlocked:
                if hasattr(self, "_cf_bypass"):
                    return self._cf_bypass()
                return self._result({"ok": False, "msg": "Cloudflare/WAF 拦截且未实现绕过"})
            except Exception as e:
                self.logger.warning(f"{self.site_name}/{self.account.name} 复用时异常: {e}")
        # 全新登录
        try:
            auth = self.login()
        except CloudflareBlocked:
            self.logger.warning(
                f"{self.site_name}/{self.account.name} 登录被 WAF 拦截，尝试浏览器绕过"
            )
            if hasattr(self, "_cf_bypass"):
                return self._cf_bypass()
            return self._result({"ok": False, "msg": "Cloudflare/WAF 拦截且未实现绕过"})
        self.store.save(self.site_name, self.account.name, auth)
        self.apply_auth(auth)
        try:
            res = self.checkin(auth)
        except AuthExpired:
            self.logger.warning(
                f"{self.site_name}/{self.account.name} 全新登录后签到仍 401，尝试浏览器绕过"
            )
            if hasattr(self, "_cf_bypass"):
                return self._cf_bypass()
            return self._result({"ok": False, "msg": "登录后签到仍返回 401"})
        except CloudflareBlocked:
            if hasattr(self, "_cf_bypass"):
                return self._cf_bypass()
            return self._result({"ok": False, "msg": "Cloudflare/WAF 拦截且未实现绕过"})
        return self._result(res)
