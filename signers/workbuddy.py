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
- 查签到活动状态：POST https://copilot.tencent.com/v2/billing/meter/checkin-activity-status
- 查账号资源余额：POST https://copilot.tencent.com/v2/billing/meter/get-user-resource
"""
import json
import os

from .base import AuthExpired, BaseSigner

CHECKIN_URL = "https://copilot.tencent.com/v2/billing/meter/daily-checkin"
STATUS_URL = "https://copilot.tencent.com/v2/billing/meter/checkin-activity-status"
RESOURCE_URL = "https://copilot.tencent.com/v2/billing/meter/get-user-resource"

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
        token = self.account.token
        token_env = self.account.token_env

        # 1. 环境变量
        if not token and token_env:
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

    def is_cached_auth_current(self, auth):
        current_token = self.account.token
        if not current_token and self.account.token_env:
            current_token = os.environ.get(self.account.token_env)
        return bool(current_token and auth.get("token") == current_token)

    def apply_auth(self, auth):
        """认证头改为按请求传递（见 _auth_headers），这里无需操作共享 session。"""

    @staticmethod
    def _auth_headers(auth):
        """按请求构造认证头（不写入共享 session 的全局 headers）。"""
        headers = dict(API_HEADERS)
        token = auth.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def checkin(self, auth):
        """调用签到 API 执行每日签到，并查询账号实际余额。"""
        s = self.session
        headers = self._auth_headers(auth)
        try:
            resp = s.post(CHECKIN_URL, json={}, headers=headers, timeout=30)
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

        # 签到后查询签到活动状态（连续天数、本次获得）和账号实际余额
        activity = self._get_activity_status(headers)
        total_credits = self._get_total_credits(headers)

        today_credit = activity.get("today_credit", 0) if activity else 0
        streak = activity.get("streak_days", 0) if activity else 0
        # 积分格式化：整数不带小数点，非整数保留2位
        credits_str = f"{total_credits:g}" if isinstance(total_credits, float) else str(total_credits)

        # code=0 签到成功
        if code == 0:
            return {
                "ok": True,
                "points": total_credits,
                "points_unit": "积分",
                "awarded": today_credit,
                "streak": streak,
                "msg": f"签到成功 +{today_credit}积分，连续{streak}天，余额{credits_str}积分",
            }

        # code=10001 "今天已签到，请明天再来" 也算成功
        if code == 10001 or "已签到" in msg or "already" in msg.lower():
            return {
                "ok": True,
                "points": total_credits,
                "points_unit": "积分",
                "awarded": today_credit,
                "streak": streak,
                "msg": f"今日已签到 +{today_credit}积分，连续{streak}天，余额{credits_str}积分",
            }

        return {"ok": False, "points": 0, "msg": msg or f"签到失败 (code={code})"}

    def _get_activity_status(self, headers):
        """查询签到活动状态（连续天数、本次获得积分等）。"""
        s = self.session
        try:
            resp = s.post(STATUS_URL, json={}, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {}) or {}
        except Exception as e:
            self.logger.warning(f"WorkBuddy/{self.account.name} 查询签到状态失败: {e}")
        return {}

    def _get_total_credits(self, headers):
        """查询账号实际可用积分余额（有效裂变包的剩余之和）。

        资源包分两类：
        - CapacityType=4：体验版（500积分试用包），不算真实余额
        - CapacityType=1：裂变包（签到/活动获得），才是真实积分
        只统计 CapacityType=1 且 Status=0（有效未过期）的裂变包。
        用 CapacityRemainPrecise 字符串字段获取小数精度（如 2.96）。
        """
        s = self.session
        try:
            resp = s.post(RESOURCE_URL, json={}, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                accounts = (
                    data.get("data", {})
                    .get("Response", {})
                    .get("Data", {})
                    .get("Accounts", [])
                )
                total = 0.0
                for pkg in accounts:
                    # 只统计裂变包（Type=1），排除体验版（Type=4）
                    if pkg.get("CapacityType") != 1:
                        continue
                    if pkg.get("Status") != 0:
                        continue
                    precise = pkg.get("CapacityRemainPrecise")
                    if precise:
                        try:
                            total += float(precise)
                        except (ValueError, TypeError):
                            total += pkg.get("CapacityRemain", 0)
                    else:
                        total += pkg.get("CapacityRemain", 0)
                return round(total, 2)
        except Exception as e:
            self.logger.warning(f"WorkBuddy/{self.account.name} 查询资源余额失败: {e}")
        return 0
