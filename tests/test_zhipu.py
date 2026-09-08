"""智谱清言签到器与聚合 Secret 解析测试（离线，不发真实请求）。"""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import Account, Config, _parse_zhipu_aggregate
from signers.zhipu import (
    CHECKIN_URL,
    REFRESH_URL,
    SCORE_URL,
    ZhipuSigner,
    _jwt_claims,
    _sign_headers,
    _vj_timestamp,
)


def zhipu_site_config():
    return {
        "sites": {
            "zhipu": {
                "enabled": True,
                "type": "zhipu",
                "base_url": "https://chatglm.cn",
                "accounts": [],
            }
        }
    }


def fake_jwt(sub="user_T9", uid="uid-1", exp=9999999999, device_id="dev-1", jtype="access"):
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload = json.dumps(
        {"sub": sub, "uid": uid, "exp": exp, "device_id": device_id, "type": jtype}
    ).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"{header}.{body}.signature"


class ZhipuAggregateTest(unittest.TestCase):
    def test_parse_aggregate_with_refresh_token(self):
        accounts = _parse_zhipu_aggregate(
            json.dumps(
                {
                    "version": 1,
                    "accounts": [
                        {
                            "key": "zp-0123456789ab",
                            "name": "测试号",
                            "access_token": "a.b.c",
                            "refresh_token": "r.s.t",
                        }
                    ],
                }
            )
        )
        self.assertEqual(accounts[0].stable_key, "zp-0123456789ab")
        self.assertEqual(accounts[0].token, "a.b.c")
        self.assertEqual(accounts[0].refresh_token, "r.s.t")

    def test_rejects_invalid_aggregates(self):
        for raw in [
            "not json",
            '{"version":2,"accounts":[]}',
            '{"version":1,"accounts":[{"key":"wb-x","access_token":"k"}]}',
            '{"version":1,"accounts":[{"key":"zp-0123456789ab"}]}',
            '{"version":1,"accounts":[{"key":"zp-0123456789ab","access_token":"a"},'
            '{"key":"zp-0123456789ab","access_token":"b"}]}',
        ]:
            with self.assertRaises(ValueError, msg=raw):
                _parse_zhipu_aggregate(raw)

    def test_config_resolves_refresh_token_env(self):
        config = Config(
            {
                "sites": {
                    "zhipu": {
                        "enabled": True,
                        "type": "zhipu",
                        "accounts": [
                            {
                                "name": "main",
                                "token_env": "ZHIPU_TOKEN",
                                "refresh_token_env": "ZHIPU_REFRESH",
                            }
                        ],
                    }
                }
            },
            Mock(),
        )
        with patch.dict(
            os.environ, {"ZHIPU_TOKEN": "a.b.c", "ZHIPU_REFRESH": "r.s.t"}
        ):
            sites = config.sites()
        self.assertEqual(len(sites), 1)
        account = sites[0].accounts[0]
        self.assertEqual(account.token, "a.b.c")
        self.assertEqual(account.refresh_token, "r.s.t")


class ZhipuSignTest(unittest.TestCase):
    def test_vj_timestamp_replaces_second_last_digit(self):
        # 1788852882000: 各位和=57, 倒数第二位=0 -> (57-0)%10=7
        self.assertEqual(_vj_timestamp(1788852882000), "1788852882070")

    def test_sign_headers_shape(self):
        headers = _sign_headers("dev-1")
        self.assertEqual(headers["App-Name"], "chatglm")
        self.assertEqual(headers["X-App-Platform"], "pc")
        self.assertEqual(headers["X-Device-Id"], "dev-1")
        self.assertEqual(len(headers["X-Nonce"]), 32)
        self.assertEqual(len(headers["X-Timestamp"]), 13)
        # sign = md5("ts-nonce-salt") 可复算验证
        import hashlib

        expect = hashlib.md5(
            f"{headers['X-Timestamp']}-{headers['X-Nonce']}-"
            "8a1317a7468aa3ad86e997d08f3f31cb".encode()
        ).hexdigest()
        self.assertEqual(headers["X-Sign"], expect)

    def test_jwt_claims_parses_exp_and_device(self):
        claims = _jwt_claims(fake_jwt(exp=123, device_id="d9"))
        self.assertEqual(claims["exp"], 123)
        self.assertEqual(claims["device_id"], "d9")
        self.assertEqual(_jwt_claims("garbage"), {})


class ZhipuSignerTest(unittest.TestCase):
    def make_signer(self, token, refresh_token=None):
        site = Mock()
        site.key = "zhipu"
        site.raw = {}
        account = Account(name="测试", token=token, refresh_token=refresh_token)
        return ZhipuSigner(site, account, Mock(), Mock(), Mock(), Mock())

    def test_login_refreshes_access_token_when_refresh_present(self):
        signer = self.make_signer(fake_jwt(exp=1000), fake_jwt(jtype="refresh"))
        with patch.object(
            signer, "_refresh_access_token", return_value=fake_jwt(exp=2000)
        ) as refresh:
            auth = signer.login()
        refresh.assert_called_once()
        self.assertNotEqual(auth["token"], fake_jwt(exp=1000))
        self.assertEqual(auth["expires_at"], 2000 - 300)

    def test_login_falls_back_to_stale_token_when_refresh_fails(self):
        signer = self.make_signer(fake_jwt(exp=1000), fake_jwt(jtype="refresh"))
        with patch.object(signer, "_refresh_access_token", return_value=None):
            auth = signer.login()
        self.assertEqual(auth["token"], fake_jwt(exp=1000))

    def test_login_without_refresh_uses_token_directly(self):
        signer = self.make_signer(fake_jwt(exp=1000))
        auth = signer.login()
        self.assertEqual(auth["token"], fake_jwt(exp=1000))

    def test_refresh_request_uses_signed_headers_and_bearer(self):
        signer = self.make_signer("a.b.c", "r.s.t")
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": 0,
            "result": {"access_token": "new.a.b", "refresh_token": "r.s.t"},
        }
        signer.session.post.return_value = response
        result = signer._refresh_access_token("r.s.t")
        self.assertEqual(result, "new.a.b")
        args, kwargs = signer.session.post.call_args
        self.assertEqual(args[0], REFRESH_URL)
        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer r.s.t")
        self.assertIn("X-Sign", headers)
        self.assertIn("X-Nonce", headers)
        self.assertEqual(kwargs["json"], {"refresh_token": "r.s.t"})

    def test_refresh_returns_none_on_auth_failure(self):
        signer = self.make_signer("a.b.c", "r.s.t")
        response = Mock(status_code=401)
        signer.session.post.return_value = response
        self.assertIsNone(signer._refresh_access_token("r.s.t"))

    def test_checkin_reports_already_claimed(self):
        signer = self.make_signer(fake_jwt())
        response = Mock(status_code=200)
        response.json.return_value = {"status": 10001, "message": "今日已领取"}
        signer.session.post.return_value = response
        score_response = Mock(status_code=200)
        score_response.json.return_value = {
            "status": 0,
            "result": {"current_score": 948},
        }
        signer.session.get.return_value = score_response
        result = signer.checkin({"token": fake_jwt()})
        self.assertTrue(result["ok"])
        self.assertEqual(result["points"], 948)
        self.assertIn("今日已领取", result["msg"])

    def test_checkin_raises_auth_expired_on_401(self):
        signer = self.make_signer(fake_jwt())
        response = Mock(status_code=401)
        signer.session.post.return_value = response
        with self.assertRaises(Exception):
            signer.checkin({"token": fake_jwt()})

    def test_endpoints_never_mixed(self):
        # 签到/积分走 member-api（免签名）；刷新走 user-api（签名）。
        self.assertIn("member-api", CHECKIN_URL)
        self.assertIn("member-api", SCORE_URL)
        self.assertIn("user-api", REFRESH_URL)


if __name__ == "__main__":
    unittest.main()
