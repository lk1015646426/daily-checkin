"""TRAE (trae.cn) 签到站（API 直连）。

认证信息来自 TRAE 桌面客户端：每个账号分别使用自己的 refresh token
和 telemetry.devDeviceId。签到状态接口提供当天奖励，权益用量接口提供
账户当前剩余积分。
"""
import base64
import binascii
import json
import math
import os
import time

from .base import AuthExpired, BaseSigner

DEFAULT_BASE_URL = "https://api.trae.cn"

API_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://api.trae.cn",
    "Referer": "https://api.trae.cn/",
}


class TraeSigner(BaseSigner):
    type = "trae"

    def login(self):
        """读取该账号的 TRAE refresh token 和独立设备 ID。"""
        token = None
        captured_device_id = None
        token_env = self.account.token_env
        device_env = self.account.device_env

        if token_env:
            token = os.environ.get(token_env)
            if token:
                self.logger.info(
                    f"trae/{self.account.name} 从环境变量 {token_env} 读取 token"
                )

        # 本地开发回退。该文件已由 .gitignore 排除，不能提交。
        if not token:
            captured_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "captured_tokens.json",
            )
            if os.path.exists(captured_file):
                try:
                    with open(captured_file, "r", encoding="utf-8") as f:
                        captured = json.load(f)
                    for uid, info in captured.items():
                        username = info.get("username", "") or ""
                        alias = (
                            username[:3] + username[-4:]
                            if len(username) >= 7
                            else username
                        )
                        if (
                            username == self.account.name
                            or uid == self.account.name
                            or alias == self.account.name
                        ):
                            token = info.get("token")
                            captured_device_id = info.get("device_id")
                            if token:
                                self.logger.info(
                                    f"trae/{self.account.name} "
                                    "从 captured_tokens.json 读取 token"
                                )
                                break
                except Exception as e:
                    self.logger.warning(f"读取 captured_tokens.json 失败: {e}")

        if not token:
            raise RuntimeError(
                f"缺少 TRAE refresh token：请设置环境变量 {token_env}，"
                "或确保 captured_tokens.json 存在且包含该账号"
            )

        device_id = os.environ.get(device_env) if device_env else None
        device_id = device_id or captured_device_id
        if not device_id:
            env_name = device_env or "该账号的 device_env"
            raise RuntimeError(f"缺少 TRAE 设备 ID：请设置环境变量 {env_name}")

        return {
            "token": token,
            "device_id": device_id,
            "expires_at": self._jwt_expiry(token),
        }

    @staticmethod
    def _jwt_expiry(token):
        """离线读取 JWT payload 中的 exp；格式不合法时交由服务端判定。"""
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
            exp = data.get("exp")
            return int(exp) if exp is not None else None
        except (
            IndexError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            binascii.Error,
            UnicodeDecodeError,
        ):
            return None

    def is_cached_auth_current(self, auth):
        """只有当前 Token、设备 ID 和缓存 JWT 均有效时才复用缓存。"""
        token_env = self.account.token_env
        device_env = self.account.device_env
        current_token = os.environ.get(token_env) if token_env else None
        current_device_id = os.environ.get(device_env) if device_env else None

        # TRAE 的 Token 与设备 ID 都是必需的；禁止用缓存补齐缺失的 Secret。
        if token_env and not current_token:
            return False
        if device_env and not current_device_id:
            return False
        if current_token and current_token != auth.get("token"):
            return False
        if current_device_id and current_device_id != auth.get("device_id"):
            return False

        now = time.time()
        cached_expiry = auth.get("expires_at")
        if cached_expiry is None:
            cached_expiry = self._jwt_expiry(auth.get("token", ""))
        if cached_expiry is not None and cached_expiry <= now:
            return False

        current_expiry = self._jwt_expiry(current_token) if current_token else None
        if current_expiry is not None and current_expiry <= now:
            return False
        return True

    def auth_expired_message(self):
        """指出受影响账号对应的 Secret 名称，但不泄露实际凭证。"""
        token_env = self.account.token_env or "该账号的 Token Secret"
        device_env = self.account.device_env or "该账号的设备 ID Secret"
        return (
            f"TRAE 账号 {self.account.name} 认证失败（401）："
            f"请重新提取该账号凭证并更新 {token_env} 和 {device_env}，"
            "同时确认二者来自同一个账号"
        )
    def apply_auth(self, auth):
        """认证头由每个 TRAE 请求显式携带，避免写入共享 session。"""

    def _headers(self, auth):
        return {
            **API_HEADERS,
            "Authorization": f"Cloud-IDE-JWT {auth.get('token', '')}",
            "x-device-id": auth.get("device_id", ""),
        }

    @staticmethod
    def _parse_credits_usage(data):
        """按 TRAE 官方客户端算法汇总积分包用量。"""
        packs = data.get("user_entitlement_pack_list") or []
        if not packs:
            return None

        limit = 0
        used = 0
        remaining = 0
        unlimited = False
        has_credits_quota = False

        for pack in packs:
            base_info = pack.get("entitlement_base_info") or {}
            quota = base_info.get("quota") or {}
            usage = pack.get("usage") or {}
            credits_limit = quota.get("credits_limit")
            credits_amount = usage.get("credits_amount", 0) or 0

            if credits_limit == -1:
                has_credits_quota = True
                unlimited = True
            elif isinstance(credits_limit, (int, float)) and credits_limit > 0:
                has_credits_quota = True
                limit += credits_limit
                remaining += max(credits_limit - credits_amount, 0)

            if isinstance(credits_limit, (int, float)) and credits_limit != 0:
                used += credits_amount

        if not has_credits_quota:
            return None

        return {
            "limit": math.inf if unlimited else limit,
            "used": used,
            "remaining": math.inf if unlimited else remaining,
            "is_credits_billing": data.get("is_credits_billing") is True,
        }

    def _post_json(self, url, headers, body):
        try:
            resp = self.session.post(
                url,
                json=body,
                headers=headers,
                timeout=30,
            )
        except Exception as e:
            raise RuntimeError(f"TRAE 请求失败: {e}") from e

        if resp.status_code == 401:
            raise AuthExpired()
        if not 200 <= resp.status_code < 300:
            raise RuntimeError(f"TRAE 请求失败 (HTTP {resp.status_code})")

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError(
                f"TRAE 返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        return resp, data

    @staticmethod
    def _business_error(data):
        """返回业务错误信息；无 code 的权益响应视为正常。"""
        if "code" not in data or data.get("code") == 0:
            return None
        code = data.get("code")
        if code == 1001:
            raise AuthExpired()
        return data.get("message") or data.get("msg") or f"code={code}"

    def _get_credits_usage(self, base, headers):
        _, data = self._post_json(
            f"{base}/trae/api/v2/pay/ide_user_ent_usage",
            headers,
            {"require_usage": True},
        )
        error = self._business_error(data)
        if error:
            raise RuntimeError(f"查询积分余额失败: {error}")

        # 兼容接口将业务数据包在 data 字段内的情况。
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        usage = self._parse_credits_usage(payload)
        if usage is None:
            raise RuntimeError("查询积分余额失败: 返回中没有可用积分包")
        return usage

    def _success_result(self, base, headers, credits, action):
        usage = self._get_credits_usage(base, headers)
        balance = usage["remaining"]
        balance_text = "无限" if math.isinf(balance) else f"{balance:g}"
        return {
            "ok": True,
            "points": balance,
            "points_unit": "积分",
            "awarded": credits,
            "msg": f"{action} +{credits}积分，余额{balance_text}积分",
        }

    def checkin(self, auth):
        """执行签到，并分别返回当天奖励和账户剩余积分。"""
        base = (self.site.base_url or DEFAULT_BASE_URL).rstrip("/")
        headers = self._headers(auth)

        _, status_data = self._post_json(
            f"{base}/trae/api/v2/ug/checkin_credits/status",
            headers,
            {},
        )
        error = self._business_error(status_data)
        if error:
            return {"ok": False, "points": 0, "msg": f"查询签到状态失败: {error}"}

        checked_in = status_data.get("checked_in", False)
        credits = status_data.get("credits", 0) or 0
        enabled = status_data.get("enable", False)

        if not enabled:
            return {"ok": False, "points": 0, "msg": "签到功能未开启"}

        if checked_in:
            return self._success_result(base, headers, credits, "今日已签到")

        _, claim_data = self._post_json(
            f"{base}/trae/api/v2/ug/checkin_credits/claim",
            headers,
            {},
        )
        claim_error = self._business_error(claim_data)
        if not claim_error:
            return self._success_result(base, headers, credits, "签到成功")

        if "已签到" in claim_error or "already" in claim_error.lower():
            return self._success_result(base, headers, credits, "今日已签到")

        return {"ok": False, "points": 0, "msg": f"签到失败: {claim_error}"}
