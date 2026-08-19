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
        """将缓存的登录态应用到请求会话。

        只应用 cookies（requests 按域隔离发送，安全）。
        认证头（Authorization 等）一律不写入共享 session 的全局 headers，
        由各 signer 在每次请求时通过 headers= 参数显式传递，
        避免 A 站的认证头被发往 B 站服务器。
        """
        if auth.get("cookies"):
            self.session.cookies.update(auth["cookies"])

    def credential_key(self):
        return getattr(self.account, "stable_key", None) or self.account.name

    def is_cached_auth_current(self, auth):
        """缓存认证是否仍与当前外部凭证一致；子类可按需覆盖。"""
        return True

    def auth_expired_message(self):
        """全新认证仍失效时返回给通知的安全提示。"""
        return "登录后签到仍返回 401"

    def _result(self, res, cached=False):
        result = {
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
        for key in (
            "stage",
            "business_code",
            "http_status",
            "claim_attempted",
            "token_expiry_state",
            "device_present",
        ):
            if key in res:
                result[key] = res.get(key)
        return result

    def run(self):
        auth = self.store.get(self.site_name, self.credential_key())
        if auth and not self.store.is_expired(auth) and self.is_cached_auth_current(auth):
            self.apply_auth(auth)
            try:
                res = self.checkin(auth)
            except AuthExpired:
                self.logger.info(f"{self.site_name}/{self.account.name} 登录态失效，重新登录")
            except CloudflareBlocked:
                if hasattr(self, "_cf_bypass"):
                    return self._cf_bypass()
                return self._result({"ok": False, "msg": "Cloudflare/WAF 拦截且未实现绕过"})
            except Exception as e:
                self.logger.warning(f"{self.site_name}/{self.account.name} 复用时异常: {e}")
            else:
                # checkin 正常返回即代表服务器已给出明确应答（无论成败）。
                # 业务失败（如"操作太过频繁"）时重登重签不会改变结果——
                # 多数站点的 login 只是重读环境变量里的同一凭证——只会
                # 重复请求、加重服务端风控，因此直接返回结果。
                return self._result(res, cached=True)
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
        self.store.save(self.site_name, self.credential_key(), auth)
        self.apply_auth(auth)
        try:
            res = self.checkin(auth)
        except AuthExpired:
            self.logger.warning(
                f"{self.site_name}/{self.account.name} 全新登录后签到仍 401，尝试浏览器绕过"
            )
            if hasattr(self, "_cf_bypass"):
                return self._cf_bypass()
            return self._result({"ok": False, "msg": self.auth_expired_message()})
        except CloudflareBlocked:
            if hasattr(self, "_cf_bypass"):
                return self._cf_bypass()
            return self._result({"ok": False, "msg": "Cloudflare/WAF 拦截且未实现绕过"})
        return self._result(res)
