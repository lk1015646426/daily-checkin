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
- 查当前可用积分：POST https://copilot.tencent.com/billing/meter/get-user-resource-summary
"""
import json
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .base import AuthExpired, BaseSigner

CHECKIN_URL = "https://copilot.tencent.com/v2/billing/meter/daily-checkin"
STATUS_URL = "https://copilot.tencent.com/v2/billing/meter/checkin-activity-status"
CREDITS_URL = "https://copilot.tencent.com/billing/meter/get-user-resource-summary"


def parse_available_credits(payload):
    """采用官方汇总的周期剩余额度；Packages 与 CreditGroups 不重复相加。

    契约参考 L0NE-6/WorkBuddy-Daily 的 queryCredits；不再按旧资源包类型排除额度。
    返回 None 表示未查询到；完整且为空的套餐列表才表示余额为零。
    """
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        return None
    data = payload.get("data")
    packages = data.get("Packages") if isinstance(data, dict) else None
    if not isinstance(packages, list):
        return None
    total = Decimal(0)
    try:
        for package in packages:
            if not isinstance(package, dict):
                return None
            raw = package.get("CycleRemainCapacity")
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
                return None
            remain = Decimal(str(raw))
            if not remain.is_finite():
                return None
            total += max(Decimal(0), remain)
        return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, OverflowError):
        return None

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
        credits_text = f"{total_credits:g}积分" if isinstance(total_credits, (int, float)) else "未查询到"

        # code=0 签到成功
        if code == 0:
            return {
                "ok": True,
                "points": total_credits,
                "points_unit": "积分",
                "awarded": today_credit,
                "streak": streak,
                "msg": f"签到成功 +{today_credit}积分，连续{streak}天，余额{credits_text}",
            }

        # code=10001 "今天已签到，请明天再来" 也算成功
        if code == 10001 or "已签到" in msg or "already" in msg.lower():
            return {
                "ok": True,
                "points": total_credits,
                "points_unit": "积分",
                "awarded": today_credit,
                "streak": streak,
                "msg": f"今日已签到 +{today_credit}积分，连续{streak}天，余额{credits_text}",
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
        """查询官方汇总余额，包含服务端计入的基础、购买和活动积分。"""
        s = self.session
        try:
            resp = s.post(CREDITS_URL, json={}, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                total = parse_available_credits(data)
                if total is not None:
                    return total
            self.logger.warning(f"WorkBuddy/{self.account.name} 积分汇总未返回有效余额")
        except Exception as e:
            self.logger.warning(f"WorkBuddy/{self.account.name} 查询积分汇总失败: {type(e).__name__}")
        return None
