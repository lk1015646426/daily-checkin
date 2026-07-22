"""配置加载：读取 config.yaml，并从环境变量解析账号密码等敏感信息。"""
import os
from dataclasses import dataclass, field
import yaml


@dataclass
class Account:
    name: str
    user: str
    password: str


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

    def sites(self):
        """解析出启用的站点与账号列表（账号密码从环境变量读取）。"""
        out = []
        for key, sc in (self.data.get("sites") or {}).items():
            if not sc.get("enabled", True):
                continue
            accounts = []
            for a in sc.get("accounts", []) or []:
                user = os.environ.get(a["user_env"])
                pwd = os.environ.get(a["pass_env"])
                if not user or not pwd:
                    self.logger.warning(
                        f"站点 {key} 账号 {a.get('name', a['user_env'])} 缺少环境变量 "
                        f"{a['user_env']}/{a['pass_env']}，已跳过"
                    )
                    continue
                accounts.append(
                    Account(name=a.get("name", a["user_env"]), user=user, password=pwd)
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
