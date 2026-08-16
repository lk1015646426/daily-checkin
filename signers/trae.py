"""TRAE (trae.cn) 签到站（API 直连）。

认证信息来自 TRAE 桌面客户端：每个账号分别使用自己的 access token
和注册服务生成的数字设备 ID。签到状态接口提供当天奖励，权益用量接口提供
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
}


class TraeHttpError(RuntimeError):
    """TRAE 非 2xx 响应；保留脱敏诊断所需的响应与业务数据。"""

    def __init__(self, response, data=None):
        self.response = response
        self.data = data if isinstance(data, dict) else {}
        super().__init__(f"TRAE 请求失败 (HTTP {response.status_code})")


class TraeBusinessError(RuntimeError):
    """TRAE HTTP 成功但业务响应或数据形状不可用。"""

    def __init__(self, response, data, detail):
        self.response = response
        self.data = data if isinstance(data, dict) else {}
        self.detail = detail
        super().__init__(detail)


class TraeProtocolError(TraeBusinessError):
    """TRAE 返回了无法按接口契约解析的响应。"""


class TraeTransportError(TraeBusinessError):
    """TRAE 请求未获得 HTTP 响应。"""


class TraeAuthError(AuthExpired):
    """保留 HTTP/业务诊断信息的认证失效。"""

    def __init__(self, response, data=None):
        self.response = response
        self.data = data if isinstance(data, dict) else {}
        self.detail = "认证失败"
        super().__init__(self.detail)


class TraeSigner(BaseSigner):
    type = "trae"

    def login(self):
        """读取该账号的 TRAE refresh token 和独立设备 ID。"""
        token = None
        captured_device_id = None
        token_env = self.account.token_env
        device_env = self.account.device_env
        device_brand_env = self.account.device_brand_env
        device_type_env = self.account.device_type_env

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
        device_id = device_id.strip()
        if not device_id.isascii() or not device_id.isdigit():
            env_name = device_env or "该账号的 device_env"
            raise RuntimeError(
                f"TRAE 设备 ID 必须是官方数字设备 ID：请重新同步 {env_name}"
            )

        device_brand = (
            os.environ.get(device_brand_env, "").strip() if device_brand_env else ""
        )
        device_type = (
            os.environ.get(device_type_env, "").strip() if device_type_env else ""
        )

        return {
            "token": token,
            "device_id": device_id,
            "device_brand": device_brand,
            "device_type": device_type,
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
        device_brand_env = self.account.device_brand_env
        device_type_env = self.account.device_type_env
        current_token = os.environ.get(token_env) if token_env else None
        current_device_id = os.environ.get(device_env) if device_env else None
        current_device_brand = (
            os.environ.get(device_brand_env, "").strip() if device_brand_env else ""
        )
        current_device_type = (
            os.environ.get(device_type_env, "").strip() if device_type_env else ""
        )

        # TRAE 的 Token 与设备 ID 都是必需的；禁止用缓存补齐缺失的 Secret。
        if token_env and not current_token:
            return False
        if device_env and not current_device_id:
            return False
        if current_device_id:
            current_device_id = current_device_id.strip()
            if not current_device_id.isascii() or not current_device_id.isdigit():
                return False
        if current_token and current_token != auth.get("token"):
            return False
        if current_device_id and current_device_id != auth.get("device_id"):
            return False
        if device_brand_env and current_device_brand != auth.get("device_brand", ""):
            return False
        if device_type_env and current_device_type != auth.get("device_type", ""):
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
        headers = {
            **API_HEADERS,
            "Authorization": f"Cloud-IDE-JWT {auth.get('token', '')}",
            "x-device-id": auth.get("device_id", ""),
        }
        device_brand = (auth.get("device_brand") or "").strip()
        device_type = (auth.get("device_type") or "").strip()
        if device_brand:
            headers["x-device-brand"] = device_brand
        if device_type:
            headers["x-device-type"] = device_type
        return headers

    @classmethod
    def _token_expiry_state(cls, token):
        expiry = cls._jwt_expiry(token or "")
        if expiry is None:
            return "unknown"
        return "expired" if expiry <= time.time() else "valid"

    def _diagnostic(
        self,
        auth,
        stage,
        data=None,
        response=None,
        claim_attempted=False,
    ):
        return {
            "stage": stage,
            "business_code": (data or {}).get("code"),
            "http_status": response.status_code if response is not None else None,
            "claim_attempted": claim_attempted,
            "token_expiry_state": self._token_expiry_state(auth.get("token")),
            "device_present": bool(auth.get("device_id")),
        }

    @staticmethod
    def _parse_credits_usage(data):
        """按 TRAE 官方客户端算法汇总积分包用量。

        与切换工具 parse_work_cn_credits_from_usage 保持一致（2026-08 修正版）：
        1. 跳过 is_hide == true 的隐藏包；
        2. 跳过显式停用包（is_active == false 或 status 为
           inactive/disabled/expired 字符串）；status==0 的数值型有效包不跳过；
        3. 没有 credits_limit 的包直接忽略（不能按 0 计）；
        4. credits_limit == -1 视为无限包，其用量不计入 used；
        5. 有限包：总量与用量分别累加，remaining = max(总量 - 总用量, 0)。
        """
        packs = data.get("user_entitlement_pack_list") or []
        if not packs:
            return None

        limit = 0
        used = 0
        unlimited = False
        has_credits_quota = False

        for pack in packs:
            if pack.get("is_hide") is True:
                continue
            if pack.get("is_active") is False:
                continue
            status = pack.get("status")
            if isinstance(status, str) and status.lower() in (
                "inactive",
                "disabled",
                "expired",
            ):
                continue

            base_info = pack.get("entitlement_base_info") or {}
            quota = base_info.get("quota") or {}
            usage = pack.get("usage") or {}
            credits_limit = quota.get("credits_limit")
            credits_amount = usage.get("credits_amount", 0) or 0

            if not isinstance(credits_limit, (int, float)):
                continue

            has_credits_quota = True
            if credits_limit == -1:
                unlimited = True
            else:
                limit += credits_limit
                used += credits_amount

        if not has_credits_quota:
            return None

        return {
            "limit": math.inf if unlimited else limit,
            "used": used,
            "remaining": math.inf if unlimited else max(limit - used, 0),
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
        except Exception:
            raise TraeTransportError(None, {}, "网络请求失败") from None

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            if resp.status_code == 401:
                raise TraeAuthError(resp) from None
            if not 200 <= resp.status_code < 300:
                raise TraeHttpError(resp) from None
            raise TraeProtocolError(
                resp,
                {},
                f"返回非 JSON (HTTP {resp.status_code})",
            ) from None
        if resp.status_code == 401:
            raise TraeAuthError(resp, data)
        if not isinstance(data, dict):
            if not 200 <= resp.status_code < 300:
                raise TraeHttpError(resp)
            raise TraeProtocolError(
                resp,
                {},
                f"返回 JSON 顶层类型无效 (HTTP {resp.status_code})",
            )
        if not 200 <= resp.status_code < 300:
            raise TraeHttpError(resp, data)
        return resp, data

    def _response_error_result(self, auth, stage, error, prefix, claim_attempted):
        response = getattr(error, "response", None)
        http_status = response.status_code if response is not None else None
        detail = (
            error.data.get("message")
            or error.data.get("msg")
            or getattr(error, "detail", None)
            or (f"HTTP {http_status}" if http_status is not None else "请求失败")
        )
        return {
            "ok": False,
            "points": 0,
            "msg": f"{prefix}: {detail}",
            **self._diagnostic(
                auth,
                stage,
                data=error.data,
                response=response,
                claim_attempted=claim_attempted,
            ),
        }

    @staticmethod
    def _business_error(data):
        """返回业务错误信息；无 code 的权益响应视为正常。"""
        if "code" not in data or data.get("code") == 0:
            return None
        code = data.get("code")
        return data.get("message") or data.get("msg") or f"code={code}"

    def _get_credits_usage(self, base, headers):
        response, data = self._post_json(
            f"{base}/trae/api/v2/pay/ide_user_ent_usage",
            headers,
            {"require_usage": True},
        )
        if data.get("code") == 1001:
            raise TraeAuthError(response, data)
        error = self._business_error(data)
        if error:
            raise TraeBusinessError(response, data, error)

        # 兼容接口将业务数据包在 data 字段内的情况。
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        usage = self._parse_credits_usage(payload)
        if usage is None:
            raise TraeBusinessError(response, data, "返回中没有可用积分包")
        return usage

    def _success_result(
        self,
        base,
        headers,
        auth,
        credits,
        action,
        stage,
        data,
        response,
        claim_attempted,
    ):
        try:
            usage = self._get_credits_usage(base, headers)
        except (TraeHttpError, TraeBusinessError, TraeAuthError) as error:
            return self._response_error_result(
                auth,
                "usage",
                error,
                "查询积分余额失败",
                claim_attempted,
            )
        balance = usage["remaining"]
        balance_text = "无限" if math.isinf(balance) else f"{balance:g}"
        return {
            "ok": True,
            "points": balance,
            "points_unit": "积分",
            "awarded": credits,
            "msg": f"{action} +{credits}积分，余额{balance_text}积分",
            **self._diagnostic(
                auth,
                stage,
                data=data,
                response=response,
                claim_attempted=claim_attempted,
            ),
        }

    def checkin(self, auth):
        """执行签到，并分别返回当天奖励和账户剩余积分。"""
        base = (self.site.base_url or DEFAULT_BASE_URL).rstrip("/")
        headers = self._headers(auth)

        try:
            status_response, status_data = self._post_json(
                f"{base}/trae/api/v2/ug/checkin_credits/status",
                headers,
                {},
            )
        except (TraeHttpError, TraeBusinessError) as error:
            return self._response_error_result(
                auth,
                "status",
                error,
                "查询签到状态失败",
                False,
            )
        error = self._business_error(status_data)
        if error:
            return {
                "ok": False,
                "points": 0,
                "msg": f"查询签到状态失败: {error}",
                **self._diagnostic(
                    auth,
                    "status",
                    data=status_data,
                    response=status_response,
                    claim_attempted=False,
                ),
            }

        checked_in = status_data.get("checked_in", False)
        credits = status_data.get("credits", 0) or 0
        enabled = status_data.get("enable", False)

        if not enabled:
            return {
                "ok": False,
                "points": 0,
                "msg": "签到功能未开启",
                **self._diagnostic(
                    auth,
                    "status",
                    data=status_data,
                    response=status_response,
                    claim_attempted=False,
                ),
            }

        if checked_in:
            return self._success_result(
                base,
                headers,
                auth,
                credits,
                "今日已签到（本次未发起领取）",
                "status",
                status_data,
                status_response,
                False,
            )

        try:
            claim_response, claim_data = self._post_json(
                f"{base}/trae/api/v2/ug/checkin_credits/claim",
                headers,
                {},
            )
        except (TraeHttpError, TraeBusinessError) as error:
            return self._response_error_result(
                auth,
                "claim",
                error,
                "签到失败",
                True,
            )
        claim_error = self._business_error(claim_data)
        if not claim_error:
            return self._success_result(
                base,
                headers,
                auth,
                credits,
                "本次领取成功",
                "claim",
                claim_data,
                claim_response,
                True,
            )

        if "已签到" in claim_error or "already" in claim_error.lower():
            return self._success_result(
                base,
                headers,
                auth,
                credits,
                "今日已签到（领取接口确认）",
                "claim",
                claim_data,
                claim_response,
                True,
            )

        return {
            "ok": False,
            "points": 0,
            "msg": f"签到失败: {claim_error}",
            **self._diagnostic(
                auth,
                "claim",
                data=claim_data,
                response=claim_response,
                claim_attempted=True,
            ),
        }
