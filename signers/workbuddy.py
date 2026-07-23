"""WorkBuddy / CodeBuddy.cn 签到站（API 直连）。

WorkBuddy（腾讯云 AI 代码助手）的每日签到只能在桌面客户端进行，
但签到背后调用的是 copilot.tencent.com 的 REST API。本签到器直接用
从客户端提取的 Keycloak access token 调用该 API，无需 Playwright/浏览器。

认证：Authorization: Bearer <accessToken>
- token 由 WorkBuddy 客户端登录后产生，存于本地 auth 文件
- CI 环境通过 GitHub Secrets（WB1_TOKEN / WB2_TOKEN）提供
- token 有效期约 1 年，过期需重新登录客户端提取

签到 API：
- 执行签到：POST https://copilot.tencent.com/v2/billing/meter/daily-checkin
- 查签到状态：POST https://copilot.tencent.com/v2/billing/meter/checkin-status
"""
import json
import os

from .base import AuthExpired, BaseSigner

CHECKIN_URL = "https://copilot.tencent.com/v2/billing/meter/daily-checkin"

# 签到 API 附加请求头（session 已有 UA / Accept）
API_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.codebuddy.cn",
    "Referer": "https://www.codebuddy.cn/",
}


class WorkBuddySigner(BaseSigner):
    type = "workbuddy"

    def login(self):
        """获取 WorkBuddy access token。

        WorkBuddy 使用手机号+验证码/微信扫码登录，CI 无法自动完成。
        token 来源优先级：
        1. 环境变量（CI: GitHub Secrets; 本地: .env）
        2. 客户端 auth 文件（本地开发: workbuddy-desktop.info）
        """
        token = None
        token_env = self.account.token_env

        # 1. 环境变量
        if token_env:
            token = os.environ.get(token_env)
            if token:
                self.logger.info(
                    f"WorkBuddy/{self.account.name} 从环境变量 {token_env} 读取 token"
                )

        # 2. 客户端 auth 文件（本地开发回退）
        if not token:
            auth_file = os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "CodeBuddyExtension", "Data", "Public", "auth",
                "workbuddy-desktop.info",
            )
            if os.path.exists(auth_file):
                try:
                    with open(auth_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    token = data.get("auth", {}).get("accessToken")
                    if token:
                        self.logger.info(
                            f"WorkBuddy/{self.account.name} 从客户端 auth 文件读取 token"
                        )
                except Exception as e:
                    self.logger.warning(f"读取 auth 文件失败: {e}")

        if not token:
            raise RuntimeError(
                f"缺少 WorkBuddy access token：请设置环境变量 {token_env}，"
                f"或确保 WorkBuddy 客户端已登录（auth 文件存在）"
            )

        return {
            "token": token,
            "expires_at": None,  # token 有效期约 1 年，不自动过期判断
        }

    def apply_auth(self, auth):
        """设置 Bearer token 认证头。"""
        token = auth.get("token")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def checkin(self, auth):
        """调用签到 API 执行每日签到。"""
        s = self.session
        try:
            resp = s.post(CHECKIN_URL, json={}, headers=API_HEADERS, timeout=30)
        except Exception as e:
            raise RuntimeError(f"签到请求失败: {e}")

        if resp.status_code == 401:
            raise AuthExpired()

        try:
            data = resp.json()
        except json.JSONDecodeError:
            raise RuntimeError(
                f"签到返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        code = data.get("code")
        msg = data.get("msg", "")

        # code=0 签到成功
        if code == 0:
            d = data.get("data", {}) or {}
            credit = d.get("today_credit") or d.get("daily_credit") or 0
            return {
                "ok": True,
                "points": credit,
                "msg": msg or "签到成功",
            }

        # code=10001 "今天已签到，请明天再来" 也算成功
        if code == 10001 or "已签到" in msg or "already" in msg.lower():
            return {"ok": True, "points": 0, "msg": msg or "今日已签到"}

        return {"ok": False, "points": 0, "msg": msg or f"签到失败 (code={code})"}
