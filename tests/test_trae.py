import base64
import json
import math
import os
import time
import unittest
from pathlib import Path

import yaml
from unittest.mock import Mock, patch

from common.config import Account, Site
from signers.base import AuthExpired
from signers.trae import TraeSigner


def make_jwt(exp):
    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': exp})}.signature"


class MemoryStore:
    def __init__(self, auth=None):
        self.auth = auth
        self.saved = []

    def get(self, site, account):
        return self.auth

    def save(self, site, account, value):
        self.auth = dict(value)
        self.saved.append((site, account, dict(value)))

    @staticmethod
    def is_expired(value):
        return False


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self.payload


class QueueSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"没有为请求准备响应: {url}")
        return self.responses.pop(0)


class ProbeTraeSigner(TraeSigner):
    def checkin(self, auth):
        return {
            "ok": True,
            "points": None,
            "msg": f"{auth['token']}|{auth['device_id']}",
        }


class TraeTestBase(unittest.TestCase):
    def make_signer(
        self,
        account=None,
        store=None,
        signer_cls=TraeSigner,
        session=None,
    ):
        account = account or Account(
            name="1780293",
            token_env="TRAE1_TOKEN",
            device_env="TRAE1_DEVICE_ID",
        )
        site = Site(
            key="trae",
            type="trae",
            base_url="https://api.trae.cn",
            enabled=True,
            accounts=[account],
        )
        return signer_cls(
            site=site,
            account=account,
            session=session or Mock(),
            store=store or MemoryStore(),
            logger=Mock(),
            notifier=Mock(),
        )


class TraeAuthenticationTests(TraeTestBase):
    def test_login_uses_account_specific_device_id_and_jwt_expiry(self):
        token = make_jwt(1_800_000_000)
        account = Account(
            name="1780293",
            token_env="TRAE1_TOKEN",
            device_env="TRAE1_DEVICE_ID",
        )
        signer = self.make_signer(account)

        with patch.dict(
            os.environ,
            {
                "TRAE1_TOKEN": token,
                "TRAE1_DEVICE_ID": "device-for-account-1",
                "TRAE_DEVICE_ID": "shared-device-must-not-be-used",
            },
            clear=False,
        ):
            auth = signer.login()

        self.assertEqual(token, auth["token"])
        self.assertEqual("device-for-account-1", auth["device_id"])
        self.assertEqual(1_800_000_000, auth["expires_at"])

    def test_run_replaces_cache_when_environment_token_changes(self):
        account = Account(
            name="1780293",
            token_env="TRAE1_TOKEN",
            device_env="TRAE1_DEVICE_ID",
        )
        store = MemoryStore(
            {
                "token": "cached-old-token",
                "device_id": "device-for-account-1",
                "expires_at": None,
            }
        )
        signer = self.make_signer(account, store, ProbeTraeSigner)

        with patch.dict(
            os.environ,
            {
                "TRAE1_TOKEN": "secret-new-token",
                "TRAE1_DEVICE_ID": "device-for-account-1",
            },
            clear=False,
        ):
            result = signer.run()

        self.assertEqual("secret-new-token|device-for-account-1", result["msg"])
        self.assertFalse(result["cached"])
        self.assertEqual("secret-new-token", store.auth["token"])

    def test_run_replaces_cache_when_account_device_id_changes(self):
        account = Account(
            name="1920293",
            token_env="TRAE2_TOKEN",
            device_env="TRAE2_DEVICE_ID",
        )
        store = MemoryStore(
            {
                "token": "same-token",
                "device_id": "wrong-account-device",
                "expires_at": None,
            }
        )
        signer = self.make_signer(account, store, ProbeTraeSigner)

        with patch.dict(
            os.environ,
            {
                "TRAE2_TOKEN": "same-token",
                "TRAE2_DEVICE_ID": "device-for-account-2",
            },
            clear=False,
        ):
            result = signer.run()

        self.assertEqual("same-token|device-for-account-2", result["msg"])
        self.assertFalse(result["cached"])
        self.assertEqual("device-for-account-2", store.auth["device_id"])


class TraeCreditsTests(TraeTestBase):
    def test_parse_credits_usage_sums_remaining_finite_packs(self):
        usage = TraeSigner._parse_credits_usage(
            {
                "is_credits_billing": True,
                "user_entitlement_pack_list": [
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 1000}
                        },
                        "usage": {"credits_amount": 200},
                    },
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 500}
                        },
                        "usage": {"credits_amount": 100},
                    },
                ],
            }
        )

        self.assertEqual(1500, usage["limit"])
        self.assertEqual(300, usage["used"])
        self.assertEqual(1200, usage["remaining"])
        self.assertTrue(usage["is_credits_billing"])

    def test_parse_credits_usage_identifies_unlimited_pack(self):
        usage = TraeSigner._parse_credits_usage(
            {
                "user_entitlement_pack_list": [
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": -1}
                        },
                        "usage": {"credits_amount": 25},
                    }
                ]
            }
        )

        self.assertTrue(math.isinf(usage["limit"]))
        self.assertTrue(math.isinf(usage["remaining"]))
        # 无限包的用量不计入 used（与切换工具口径一致）
        self.assertEqual(0, usage["used"])

    def test_parse_credits_usage_uses_total_minus_used_not_per_pack_floor(self):
        """超支包场景：官方口径是总量-总用量，不是每包各自封顶再相加。"""
        usage = TraeSigner._parse_credits_usage(
            {
                "user_entitlement_pack_list": [
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 100}
                        },
                        "usage": {"credits_amount": 150},
                    },
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 1000}
                        },
                        "usage": {"credits_amount": 250},
                    },
                ]
            }
        )

        self.assertEqual(1100, usage["limit"])
        self.assertEqual(400, usage["used"])
        self.assertEqual(700, usage["remaining"])

    def test_parse_credits_usage_skips_hidden_and_counts_status_zero_packs(self):
        """隐藏包跳过；status==0（数值）的有效积分包必须计入。"""
        usage = TraeSigner._parse_credits_usage(
            {
                "user_entitlement_pack_list": [
                    {
                        "is_hide": True,
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 999}
                        },
                        "usage": {"credits_amount": 1},
                    },
                    {
                        "status": 0,
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 2000}
                        },
                        "usage": {"credits_amount": 100},
                    },
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 2000}
                        },
                        "usage": {"credits_amount": 300},
                    },
                ]
            }
        )

        self.assertEqual(4000, usage["limit"])
        self.assertEqual(400, usage["used"])
        self.assertEqual(3600, usage["remaining"])

    def test_parse_credits_usage_supports_decimal_quota(self):
        """credits_amount/credits_limit 带小数时按浮点计算。"""
        usage = TraeSigner._parse_credits_usage(
            {
                "user_entitlement_pack_list": [
                    {
                        "entitlement_base_info": {
                            "quota": {"credits_limit": 2000.5}
                        },
                        "usage": {"credits_amount": 864.63},
                    }
                ]
            }
        )

        self.assertAlmostEqual(1135.87, usage["remaining"], places=6)

    def test_checkin_claims_then_returns_daily_award_and_current_balance(self):
        session = QueueSession(
            [
                FakeResponse(
                    {"code": 0, "checked_in": False, "credits": 200, "enable": True}
                ),
                FakeResponse({"code": 0}),
                FakeResponse(
                    {
                        "user_entitlement_pack_list": [
                            {
                                "entitlement_base_info": {
                                    "quota": {"credits_limit": 2000}
                                },
                                "usage": {"credits_amount": 400},
                            }
                        ]
                    }
                ),
            ]
        )
        signer = self.make_signer(session=session)

        result = signer.checkin({"token": "token", "device_id": "device-1"})

        self.assertTrue(result["ok"])
        self.assertEqual(200, result["awarded"])
        self.assertEqual(1600, result["points"])
        self.assertEqual("积分", result["points_unit"])
        self.assertEqual(3, len(session.calls))
        self.assertTrue(session.calls[1][0].endswith("/claim"))
        self.assertTrue(session.calls[2][0].endswith("/ide_user_ent_usage"))
        self.assertEqual(
            {"require_usage": True}, session.calls[2][1]["json"]
        )

    def test_already_checked_in_still_returns_daily_award_and_balance(self):
        session = QueueSession(
            [
                FakeResponse(
                    {"code": 0, "checked_in": True, "credits": 200, "enable": True}
                ),
                FakeResponse(
                    {
                        "user_entitlement_pack_list": [
                            {
                                "entitlement_base_info": {
                                    "quota": {"credits_limit": 1000}
                                },
                                "usage": {"credits_amount": 250},
                            }
                        ]
                    }
                ),
            ]
        )
        signer = self.make_signer(session=session)

        result = signer.checkin({"token": "token", "device_id": "device-1"})

        self.assertTrue(result["ok"])
        self.assertEqual(200, result["awarded"])
        self.assertEqual(750, result["points"])
        self.assertEqual(2, len(session.calls))
        self.assertFalse(any(url.endswith("/claim") for url, _ in session.calls))

    def test_balance_endpoint_401_expires_authentication(self):
        session = QueueSession(
            [
                FakeResponse(
                    {"code": 0, "checked_in": True, "credits": 200, "enable": True}
                ),
                FakeResponse({}, status_code=401),
            ]
        )
        signer = self.make_signer(session=session)

        with self.assertRaises(AuthExpired):
            signer.checkin({"token": "token", "device_id": "device-1"})

class TraeDeploymentTests(TraeTestBase):
    def test_config_maps_each_trae_account_to_its_own_device_secret(self):
        config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        accounts = config["sites"]["trae"]["accounts"]

        self.assertEqual(
            [
                {
                    "name": "1780293",
                    "token_env": "TRAE1_TOKEN",
                    "device_env": "TRAE1_DEVICE_ID",
                },
                {
                    "name": "1920293",
                    "token_env": "TRAE2_TOKEN",
                    "device_env": "TRAE2_DEVICE_ID",
                },
                {
                    "name": "账号3",
                    "token_env": "TRAE3_TOKEN",
                    "device_env": "TRAE3_DEVICE_ID",
                },
            ],
            accounts,
        )

    def test_workflow_runs_at_beijing_0400_with_independent_trae_secrets(self):
        workflow = Path(".github/workflows/daily-checkin.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('cron: "0 20 * * *"', workflow)
        self.assertIn("TRAE1_TOKEN: ${{ secrets.TRAE1_TOKEN }}", workflow)
        self.assertIn("TRAE1_DEVICE_ID: ${{ secrets.TRAE1_DEVICE_ID }}", workflow)
        self.assertIn("TRAE2_TOKEN: ${{ secrets.TRAE2_TOKEN }}", workflow)
        self.assertIn("TRAE2_DEVICE_ID: ${{ secrets.TRAE2_DEVICE_ID }}", workflow)
        self.assertIn("TRAE3_TOKEN: ${{ secrets.TRAE3_TOKEN }}", workflow)
        self.assertIn("TRAE3_DEVICE_ID: ${{ secrets.TRAE3_DEVICE_ID }}", workflow)
        self.assertNotIn("TRAE_DEVICE_ID: ${{ secrets.TRAE_DEVICE_ID }}", workflow)
        self.assertIn("key: signin-token-cache-v2-${{ github.run_id }}", workflow)
        self.assertIn("restore-keys: |", workflow)
        self.assertIn("signin-token-cache-v2-", workflow)

    def test_fresh_401_names_only_the_affected_account_secrets(self):
        session = QueueSession([FakeResponse({}, status_code=401)])
        store = MemoryStore()
        signer = self.make_signer(session=session, store=store)

        with patch.dict(
            os.environ,
            {
                "TRAE1_TOKEN": "raw-secret-must-not-appear",
                "TRAE1_DEVICE_ID": "raw-device-must-not-appear",
            },
            clear=False,
        ):
            result = signer.run()

        self.assertFalse(result["ok"])
        self.assertIn("TRAE1_TOKEN", result["msg"])
        self.assertIn("TRAE1_DEVICE_ID", result["msg"])
        self.assertNotIn("raw-secret-must-not-appear", result["msg"])
        self.assertNotIn("raw-device-must-not-appear", result["msg"])

class NotificationTests(unittest.TestCase):
    def test_unlimited_trae_balance_is_displayed_as_unlimited(self):
        from common.notify import Notifier

        summary = Notifier.format_summary(
            [
                {
                    "site": "trae",
                    "account": "1780293",
                    "ok": True,
                    "points": float("inf"),
                    "points_unit": "积分",
                    "awarded": 200,
                    "streak": None,
                    "msg": "ok",
                }
            ]
        )

        self.assertIn("余额 **无限积分**", summary)
        self.assertNotIn("inf积分", summary)
class HardeningTests(TraeTestBase):
    def test_expired_cached_jwt_is_not_reused(self):
        expired_token = make_jwt(int(time.time()) - 60)
        account = Account(
            name="1780293",
            token_env="TRAE1_TOKEN",
            device_env="TRAE1_DEVICE_ID",
        )
        store = MemoryStore(
            {
                "token": expired_token,
                "device_id": "device-1",
                "expires_at": None,
            }
        )
        signer = self.make_signer(account, store, ProbeTraeSigner)

        with patch.dict(
            os.environ,
            {"TRAE1_TOKEN": expired_token, "TRAE1_DEVICE_ID": "device-1"},
            clear=False,
        ):
            result = signer.run()

        self.assertFalse(result["cached"])

    def test_missing_device_id_invalidates_cached_auth(self):
        account = Account(
            name="1780293",
            token_env="TRAE1_TOKEN",
            device_env="TRAE1_DEVICE_ID",
        )
        signer = self.make_signer(account)

        with patch.dict(
            os.environ,
            {"TRAE1_TOKEN": "opaque-token", "TRAE1_DEVICE_ID": ""},
            clear=False,
        ):
            self.assertFalse(
                signer.is_cached_auth_current(
                    {"token": "opaque-token", "device_id": "cached-device"}
                )
            )

    def test_non_success_http_status_is_rejected_even_when_json_code_is_zero(self):
        session = QueueSession([FakeResponse({"code": 0}, status_code=500)])
        signer = self.make_signer(session=session)

        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            signer.checkin({"token": "token", "device_id": "device-1"})

    def test_invalid_jwt_expiry_is_ignored_without_crashing(self):
        self.assertIsNone(TraeSigner._jwt_expiry("not-a-valid-jwt"))
        self.assertIsNone(TraeSigner._jwt_expiry("a.%%%.c"))

    def test_configured_accounts_build_distinct_request_headers(self):
        from common.config import Config

        logger = Mock()
        with patch.dict(
            os.environ,
            {
                "TRAE1_TOKEN": "token-1",
                "TRAE1_DEVICE_ID": "device-1",
                "TRAE2_TOKEN": "token-2",
                "TRAE2_DEVICE_ID": "device-2",
            },
            clear=False,
        ):
            config = Config.load("config.yaml", logger)
            trae_site = next(site for site in config.sites() if site.type == "trae")

        signer1 = self.make_signer(trae_site.accounts[0])
        signer2 = self.make_signer(trae_site.accounts[1])
        headers1 = signer1._headers({"token": "token-1", "device_id": "device-1"})
        headers2 = signer2._headers({"token": "token-2", "device_id": "device-2"})

        self.assertEqual("Cloud-IDE-JWT token-1", headers1["Authorization"])
        self.assertEqual("device-1", headers1["x-device-id"])
        self.assertEqual("Cloud-IDE-JWT token-2", headers2["Authorization"])
        self.assertEqual("device-2", headers2["x-device-id"])

    def test_trae_notification_uses_consistent_display_name(self):
        from common.notify import Notifier

        summary = Notifier.format_summary(
            [
                {
                    "site": "trae",
                    "account": "1780293",
                    "ok": True,
                    "points": 1600,
                    "points_unit": "积分",
                    "awarded": 200,
                    "streak": None,
                    "msg": "ok",
                }
            ]
        )

        self.assertIn("TRAE 1780293", summary)
        self.assertNotIn("trae 1780293", summary)
if __name__ == "__main__":
    unittest.main()

