"""配置加载：读取 config.yaml，并从环境变量解析登录凭证。

凭证支持三类：
- 账号密码类（如 acy7）：user_env + pass_env
- 登录态类（如 WorkBuddy 旧方案）：cookies_env（整段 cookies JSON）
- Token 类（如 WorkBuddy/TRAE）：token_env（token 字符串）
- 账号附加设备标识（如 TRAE）：device_env
"""
import os
from dataclasses import dataclass, field
import yaml


@dataclass
class Account:
    name: str
    user: str = None
    password: str = None
    cookies_env: str = None
    token_env: str = None
    device_env: str = None


@dataclass
class Site:
    key: str
    type: str
    base_url: str
    enabled: bool
    accounts: list
    raw: dict = field(default_factory=dict)


class Config:
    def __init__(self, data, logger):
        self.data = data or {}
        self.logger = logger

    @classmethod
    def load(cls, path, logger):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data, logger)

    def get_notify(self):
        return self.data.get("notify", {})

    def get_retry(self):
        return self.data.get("retry", {})

    def sites(self, require_credentials=True):
        """解析站点与账号。

        登录凭证仍由账号密码、cookies 或 token 决定是否可用；device_env
        是 token 站点的附加认证参数，不单独视为登录凭证。
        """
        out = []
        for key, sc in (self.data.get("sites") or {}).items():
            if not sc.get("enabled", True):
                continue
            accounts = []
            for a in sc.get("accounts", []) or []:
                name = (
                    a.get("name")
                    or a.get("user_env")
                    or a.get("cookies_env")
                    or a.get("token_env")
                    or key
                )
                user_env = a.get("user_env")
                pass_env = a.get("pass_env")
                cookies_env = a.get("cookies_env")
                token_env = a.get("token_env")
                device_env = a.get("device_env")
                user = os.environ.get(user_env) if user_env else None
                pwd = os.environ.get(pass_env) if pass_env else None
                cookies = os.environ.get(cookies_env) if cookies_env else None
                token = os.environ.get(token_env) if token_env else None
                has_pwd = bool(user and pwd)
                has_cookies = bool(cookies)
                has_token = bool(token)
                if require_credentials and not has_pwd and not has_cookies and not has_token:
                    self.logger.warning(
                        f"站点 {key} 账号 {name} 缺少登录凭证 "
                        f"(需 {user_env}/{pass_env} 或 {cookies_env} 或 {token_env})，已跳过"
                    )
                    continue
                accounts.append(
                    Account(
                        name=name,
                        user=user,
                        password=pwd,
                        cookies_env=cookies_env,
                        token_env=token_env,
                        device_env=device_env,
                    )
                )
            if not accounts:
                self.logger.warning(f"站点 {key} 无可用账号，已跳过")
                continue
            out.append(
                Site(
                    key=key,
                    type=sc.get("type", key),
                    base_url=sc.get("base_url", ""),
                    enabled=sc.get("enabled", True),
                    accounts=accounts,
                    raw=sc,
                )
            )
        return out
