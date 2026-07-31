"""TRAE (trae.cn) 签到站（API 直连）。

TRAE 桌面客户端引入了积分制度，每日签到可获得积分。
签到背后调用的是 api.trae.cn 的 REST API。本签到器直接用
从客户端 storage.json 提取的 refresh token 调用该 API。

认证：Authorization: Cloud-IDE-JWT <refresh_token>
- refresh_token 由 TRAE 客户端登录后产生，存于 storage.json（加密）
- 需用解密脚本从 storage.json 提取，填入 GitHub Secrets
- token 有效期 14 天，过期需重新登录客户端提取
- 直接使用 refresh_token 作为 Cloud-IDE-JWT，无需 GenerateTempToken

签到 API：
- 查签到状态：POST https://api.trae.cn/trae/api/v2/ug/checkin_credits/status
- 执行签到：POST https://api.trae.cn/trae/api/v2/ug/checkin_credits/claim
- 请求头：x-device-id: <device_id>（从 storage.json 的 telemetry.devDeviceId 提取）
- 请求体：{}（空 JSON）
"""
import json
import os

from .base import AuthExpired, BaseSigner

DEFAULT_BASE_URL = "https://api.trae.cn"
DEFAULT_DEVICE_ID = "trae-checkin-device"

API_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://api.trae.cn",
    "Referer": "https://api.trae.cn/",
}


class TraeSigner(BaseSigner):
    type = "trae"

    def login(self):
        """获取 TRAE refresh token。

        TRAE 使用手机号+验证码登录桌面客户端，CI 无法自动完成。
        token 来源优先级：
        1. 环境变量（CI: GitHub Secrets; 本地: .env）
        2. captured_tokens.json（本地开发回退）
        """
        token = None
        token_env = self.account.token_env

        # 1. 环境变量
        if token_env:
            token = os.environ.get(token_env)
            if token:
                self.logger.info(
                    f"trae/{self.account.name} 从环境变量 {token_env} 读取 token"
                )

        # 2. captured_tokens.json（本地开发回退）
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
                        if info.get("username") == self.account.name or uid == self.account.name:
                            token = info.get("token")
                            if token:
                                self.logger.info(
                                    f"trae/{self.account.name} 从 captured_tokens.json 读取 token"
                                )
                                break
                except Exception as e:
                    self.logger.warning(f"读取 captured_tokens.json 失败: {e}")

        if not token:
            raise RuntimeError(
                f"缺少 TRAE refresh token：请设置环境变量 {token_env}，"
                f"或确保 captured_tokens.json 存在且包含该账号"
            )

        # device_id 从共享环境变量读取（所有账号共用同一设备 ID）
        device_id = os.environ.get("TRAE_DEVICE_ID") or DEFAULT_DEVICE_ID

        return {
            "token": token,
            "device_id": device_id,
            "expires_at": None,  # refresh_token 有效期 14 天，不自动过期判断
        }

    def apply_auth(self, auth):
        """设置 Cloud-IDE-JWT 认证头和设备 ID 头。"""
        token = auth.get("token")
        if token:
            self.session.headers["Authorization"] = f"Cloud-IDE-JWT {token}"
        device_id = auth.get("device_id")
        if device_id:
            self.session.headers["x-device-id"] = device_id

    def checkin(self, auth):
        """调用签到 API 执行每日签到。"""
        s = self.session
        base = (self.site.base_url or DEFAULT_BASE_URL).rstrip("/")
        token = auth.get("token", "")
        device_id = auth.get("device_id", DEFAULT_DEVICE_ID)

        headers = {
            **API_HEADERS,
            "Authorization": f"Cloud-IDE-JWT {token}",
            "x-device-id": device_id,
        }

        # 1. 查询签到状态
        try:
            resp = s.post(
                f"{base}/trae/api/v2/ug/checkin_credits/status",
                json={},
                headers=headers,
                timeout=30,
            )
        except Exception as e:
            raise RuntimeError(f"查询签到状态失败: {e}")

        if resp.status_code == 401:
            raise AuthExpired()

        try:
            status_data = resp.json()
        except json.JSONDecodeError:
            raise RuntimeError(
                f"签到状态返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        code = status_data.get("code", -1)
        if code != 0:
            msg = status_data.get("message", "") or f"code={code}"
            # 1001 表示认证失败
            if code == 1001 or resp.status_code == 401:
                raise AuthExpired()
            return {"ok": False, "points": 0, "msg": f"查询签到状态失败: {msg}"}

        checked_in = status_data.get("checked_in", False)
        credits = status_data.get("credits", 0)
        enabled = status_data.get("enable", False)

        if not enabled:
            return {"ok": False, "points": 0, "msg": "签到功能未开启"}

        # 今日已签到
        if checked_in:
            return {
                "ok": True,
                "points": credits,
                "points_unit": "积分",
                "awarded": 0,
                "msg": f"今日已签到，当前积分 {credits}",
            }

        # 2. 执行签到
        try:
            resp = s.post(
                f"{base}/trae/api/v2/ug/checkin_credits/claim",
                json={},
                headers=headers,
                timeout=30,
            )
        except Exception as e:
            raise RuntimeError(f"签到请求失败: {e}")

        if resp.status_code == 401:
            raise AuthExpired()

        try:
            claim_data = resp.json()
        except json.JSONDecodeError:
            raise RuntimeError(
                f"签到返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        claim_code = claim_data.get("code", -1)
        if claim_code == 0:
            return {
                "ok": True,
                "points": credits,
                "points_unit": "积分",
                "awarded": credits,
                "msg": f"签到成功 +{credits}积分，当前积分 {credits}",
            }

        claim_msg = claim_data.get("message", "") or f"code={claim_code}"
        # 签到失败但可能是重复签到
        if "已签到" in claim_msg or "already" in claim_msg.lower():
            return {
                "ok": True,
                "points": credits,
                "points_unit": "积分",
                "awarded": 0,
                "msg": f"今日已签到，当前积分 {credits}",
            }

        return {"ok": False, "points": 0, "msg": f"签到失败: {claim_msg}"}
