"""智谱清言签到站（API 直连 + 云端自动续期）。

清言（chatglm.cn）网页/桌面客户端的「每日登录积分」：登录后每天首次访问
自动到账（客户端启动时 startDailyClickRewardMonitor 自动领取）。本签到器
直接调用背后的 member-api 完成同样的领取动作，无需浏览器。

认证：Authorization: Bearer <chatglm_token>
- 凭证由切换工具从清言桌面客户端 Cookie 同步到 GitHub Secret
  ZHIPU_ACCOUNTS_JSON（{version, accounts:[{key, name, access_token,
  refresh_token}]}），common/config.py 解析为账号；
- access token 约 24 小时有效；refresh token 约 180 天且不轮换。
  每次签到前先用 refresh token 换新 access token（云端完全自主续期，
  本地无需每天打开工具）。

API（2026-09-08 抓包验证）：
- 刷新 token：POST https://chatglm.cn/chatglm/user-api/user/refresh
  body {"refresh_token":...}，Bearer 也传 refresh token；需签名头
  （X-Timestamp/X-Nonce/X-Sign，算法逆向自前端，见 _sign_headers）
- 领取每日积分：POST https://chatglm.cn/chatglm/member-api/member/daily_login_score
  成功 {"status":0,...}；已领取 {"status":10001,"message":"今日已领取"}
- 查询积分/活动：GET https://chatglm.cn/chatglm/member-api/member/score_activity_status
  {"status":0,"result":{"current_score":948,"status":-2,...}}
"""
import base64
import hashlib
import json
import os
import time
import uuid

from .base import AuthExpired, BaseSigner

CHECKIN_URL = "https://chatglm.cn/chatglm/member-api/member/daily_login_score"
SCORE_URL = "https://chatglm.cn/chatglm/member-api/member/score_activity_status"
USER_INFO_URL = "https://chatglm.cn/chatglm/user-api/user/info"
REFRESH_URL = "https://chatglm.cn/chatglm/user-api/user/refresh"

API_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://chatglm.cn",
    "Referer": "https://chatglm.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# 签名盐：前端 JS 常量（chatglm.cn main bundle，公开可见）。
_SIGN_SALT = "8a1317a7468aa3ad86e997d08f3f31cb"


def _jwt_claims(token):
    """解析 JWT payload（不校验签名，仅读取 uid/exp/device_id 声明）。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _vj_timestamp(now_ms):
    """前端 vj() 时间戳变换：倒数第二位替换为校验位。

    checksum = (各位数字之和 - 原倒数第二位) % 10
    """
    text = str(now_ms)
    digits = [int(c) for c in text]
    n = len(text)
    checksum = (sum(digits) - digits[n - 2]) % 10
    return text[: n - 2] + str(checksum) + text[n - 1 :]


def _sign_headers(device_id, token=None):
    """构造 user-api 签名请求头（X-Timestamp/X-Nonce/X-Sign 等）。

    token 提供时同时附加 Authorization: Bearer。
    """
    ts = _vj_timestamp(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    sign = hashlib.md5(f"{ts}-{nonce}-{_SIGN_SALT}".encode()).hexdigest()
    headers = {
        "Content-Type": "application/json;charset=utf-8",
        "App-Name": "chatglm",
        "X-Device-Id": device_id,
        "X-Request-Id": uuid.uuid4().hex,
        "X-App-Platform": "pc",
        "X-App-Version": "0.0.1",
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Sign": sign,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class ZhipuSigner(BaseSigner):
    type = "zhipu"

    def login(self):
        """准备登录态：优先用 refresh token 换新 access token。

        refresh token 不轮换（每次刷新返回同一个），聚合 Secret 里的
        180 天内一直可用；刷新失败时回退旧 access token（可能仍有效）。
        """
        token = self.account.token
        token_env = self.account.token_env
        if not token and token_env:
            token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(
                f"缺少清言 access token：请设置环境变量 {token_env}，"
                f"或由切换工具同步 ZHIPU_ACCOUNTS_JSON"
            )

        refresh_token = getattr(self.account, "refresh_token", None)
        refresh_env = getattr(self.account, "refresh_token_env", None)
        if not refresh_token and refresh_env:
            refresh_token = os.environ.get(refresh_env)

        if refresh_token:
            refreshed = self._refresh_access_token(refresh_token)
            if refreshed:
                token = refreshed
                self.logger.info(
                    f"zhipu/{self.account.name} access token 已自动续期"
                )
        else:
            self.logger.warning(
                f"zhipu/{self.account.name} 无 refresh token，使用存量 access token"
                f"（约 24 小时后过期，请更新切换工具同步）"
            )

        exp = _jwt_claims(token).get("exp")
        # 提前 5 分钟视为过期，避免边界时刻失败。
        expires_at = exp - 300 if exp else None
        return {
            "token": token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }

    def _refresh_access_token(self, refresh_token):
        """用 refresh token 换新 access token（需签名头）。失败返回 None。"""
        s = self.session
        device_id = _jwt_claims(refresh_token).get("device_id") or ""
        headers = _sign_headers(device_id)
        headers["Authorization"] = f"Bearer {refresh_token}"
        try:
            resp = s.post(
                REFRESH_URL,
                json={"refresh_token": refresh_token},
                headers=headers,
                timeout=30,
            )
        except Exception as e:
            self.logger.warning(f"zhipu/{self.account.name} 刷新 token 请求失败: {e}")
            return None
        if resp.status_code in (401, 403):
            # refresh token 本身失效：需要本地重新导入（180 天窗口耗尽）。
            self.logger.error(
                f"zhipu/{self.account.name} refresh token 已失效，"
                f"请在切换工具重新导入该账号"
            )
            return None
        try:
            data = resp.json()
        except ValueError:
            self.logger.warning(
                f"zhipu/{self.account.name} 刷新响应非 JSON (HTTP {resp.status_code})"
            )
            return None
        if data.get("status") != 0:
            self.logger.warning(
                f"zhipu/{self.account.name} 刷新 token 失败: {data.get('message')}"
            )
            return None
        result = data.get("result") or {}
        new_token = result.get("access_token")
        if not new_token:
            self.logger.warning(f"zhipu/{self.account.name} 刷新响应缺少 access_token")
            return None
        return new_token

    def is_cached_auth_current(self, auth):
        """每次运行都重新 login（内部会 refresh 换最新 token），
        彻底避免 24 小时过期边界与时钟偏差。"""
        return False

    def apply_auth(self, auth):
        """认证头按请求传递（见 _auth_headers），无需操作共享 session。"""

    @staticmethod
    def _auth_headers(auth):
        headers = dict(API_HEADERS)
        token = auth.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def checkin(self, auth):
        """领取每日登录积分并查询实际余额。"""
        s = self.session
        headers = self._auth_headers(auth)
        try:
            resp = s.post(CHECKIN_URL, json=None, headers=headers, timeout=30)
        except Exception as e:
            raise RuntimeError(f"签到请求失败: {e}")
        if resp.status_code in (401, 403):
            raise AuthExpired()
        try:
            data = resp.json()
        except ValueError:
            return {
                "ok": False,
                "points": 0,
                "msg": f"签到返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}",
            }
        status = data.get("status")
        message = str(data.get("message", "") or "")
        # 签到后查询总积分（user/info 口径，与客户端一致）。
        current_score = self._get_score(auth)
        score_str = (
            f"{current_score:g}" if isinstance(current_score, (int, float)) else "未知"
        )
        if status == 0:
            return {
                "ok": True,
                "points": current_score,
                "points_unit": "积分",
                "msg": f"签到成功，当前积分 {score_str}",
            }
        if status == 10001 or "已领取" in message:
            return {
                "ok": True,
                "points": current_score,
                "points_unit": "积分",
                "msg": f"今日已领取，当前积分 {score_str}",
            }
        return {"ok": False, "points": 0, "msg": message or f"签到失败 (status={status})"}

    def _get_score(self, auth):
        """只读查询总积分（user/info 的 member_info.left_score，客户端同口径）。

        需签名头；device_id 取 JWT 声明（access/refresh token 中一致）。
        """
        s = self.session
        token = auth.get("token") or ""
        refresh = auth.get("refresh_token") or ""
        source = _jwt_claims(token) or _jwt_claims(refresh)
        device_id = source.get("device_id") or ""
        try:
            headers = _sign_headers(device_id, token=token)
            resp = s.get(USER_INFO_URL, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == 0:
                    member = (data.get("result") or {}).get("member_info") or {}
                    return member.get("left_score")
        except Exception as e:
            self.logger.warning(f"zhipu/{self.account.name} 查询积分失败: {e}")
        return None
