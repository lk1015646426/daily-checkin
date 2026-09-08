"""配置加载：读取 config.yaml，并从环境变量解析登录凭证。

凭证支持三类：
- 账号密码类（如 acy7）：user_env + pass_env
- 登录态类（如 WorkBuddy 旧方案）：cookies_env（整段 cookies JSON）
- Token 类（如 WorkBuddy/TRAE/智谱）：token_env（token 字符串）
- 账号附加设备上下文（如 TRAE）：device_env + 可选品牌/系统环境变量
"""
import json
import os
import re
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
    device_brand_env: str = None
    device_type_env: str = None
    stable_key: str = None
    token: str = None
    refresh_token: str = None
    refresh_token_env: str = None


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
            configured_accounts = sc.get("accounts", []) or []
            if key == "workbuddy" and "WORKBUDDY_ACCOUNTS_JSON" in os.environ:
                try:
                    configured_accounts = _parse_workbuddy_aggregate(
                        os.environ.get("WORKBUDDY_ACCOUNTS_JSON", "")
                    )
                except ValueError as exc:
                    # 不记录 Secret 原文；旧账号配置仍可继续运行。
                    self.logger.warning(f"WorkBuddy 聚合 Secret 无效，回退旧账号配置: {exc}")
            if key == "zhipu" and "ZHIPU_ACCOUNTS_JSON" in os.environ:
                try:
                    configured_accounts = _parse_zhipu_aggregate(
                        os.environ.get("ZHIPU_ACCOUNTS_JSON", "")
                    )
                except ValueError as exc:
                    # 不记录 Secret 原文；旧账号配置仍可继续运行。
                    self.logger.warning(f"智谱聚合 Secret 无效，回退旧账号配置: {exc}")
            for a in configured_accounts:
                if isinstance(a, Account):
                    name = a.name
                    user = a.user
                    pwd = a.password
                    cookies = None
                    token = a.token
                    user_env = a.user_env if hasattr(a, "user_env") else None
                    pass_env = None
                    cookies_env = a.cookies_env
                    token_env = a.token_env
                    device_env = a.device_env
                    device_brand_env = a.device_brand_env
                    device_type_env = a.device_type_env
                    stable_key = a.stable_key
                    refresh_token = a.refresh_token
                    refresh_token_env = a.refresh_token_env
                else:
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
                    device_brand_env = a.get("device_brand_env")
                    device_type_env = a.get("device_type_env")
                    user = os.environ.get(user_env) if user_env else None
                    pwd = os.environ.get(pass_env) if pass_env else None
                    cookies = os.environ.get(cookies_env) if cookies_env else None
                    token = os.environ.get(token_env) if token_env else None
                    refresh_token_env = a.get("refresh_token_env")
                    refresh_token = (
                        os.environ.get(refresh_token_env) if refresh_token_env else None
                    )
                    stable_key = None
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
                        device_brand_env=device_brand_env,
                        device_type_env=device_type_env,
                        stable_key=stable_key,
                        token=token,
                        refresh_token=refresh_token,
                        refresh_token_env=refresh_token_env,
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


_WORKBUDDY_KEY_RE = re.compile(r"^wb-[0-9a-f]{12,64}$")
_ZHIPU_KEY_RE = re.compile(r"^zp-[0-9a-f]{12,64}$")


def _safe_workbuddy_name(value):
    if not isinstance(value, str):
        raise ValueError("账号名称无效")
    name = "".join(char for char in value.strip() if ord(char) >= 32 and ord(char) != 127)
    if not name or len(name) > 80:
        raise ValueError("账号名称长度无效")
    return name


def _parse_workbuddy_aggregate(raw):
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON 解析失败") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
    ):
        raise ValueError("版本无效")
    entries = payload.get("accounts")
    if not isinstance(entries, list):
        raise ValueError("账号列表无效")
    accounts = []
    keys = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("账号项无效")
        key = entry.get("key")
        token = entry.get("access_token")
        if not isinstance(key, str) or not _WORKBUDDY_KEY_RE.fullmatch(key):
            raise ValueError("账号稳定键无效")
        if key in keys:
            raise ValueError("账号稳定键重复")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("账号 token 缺失")
        keys.add(key)
        accounts.append(
            Account(
                name=_safe_workbuddy_name(entry.get("name")),
                stable_key=key,
                token=token.strip(),
            )
        )
    return accounts


def _parse_zhipu_aggregate(raw):
    """解析切换工具同步的 ZHIPU_ACCOUNTS_JSON 聚合 Secret。

    结构与 WorkBuddy 聚合一致：{version, accounts:[{key, name, access_token,
    refresh_token}]}，key 为 zp- 前缀稳定账号 ID，access_token 为清言
    （chatglm.cn）登录 JWT。refresh_token 当前仅存档，签到只用 access。
    """
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON 解析失败") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
    ):
        raise ValueError("版本无效")
    entries = payload.get("accounts")
    if not isinstance(entries, list):
        raise ValueError("账号列表无效")
    accounts = []
    keys = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("账号项无效")
        key = entry.get("key")
        token = entry.get("access_token")
        if not isinstance(key, str) or not _ZHIPU_KEY_RE.fullmatch(key):
            raise ValueError("账号稳定键无效")
        if key in keys:
            raise ValueError("账号稳定键重复")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("账号 access token 缺失")
        keys.add(key)
        accounts.append(
            Account(
                name=_safe_workbuddy_name(entry.get("name")),
                stable_key=key,
                token=token.strip(),
                refresh_token=(entry.get("refresh_token") or "").strip() or None,
            )
        )
    return accounts
