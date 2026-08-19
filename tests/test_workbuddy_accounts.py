import os
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from common.config import Account, Config
from main import account_matches_filter, parse_account_filter
from signers.workbuddy import WorkBuddySigner


def workbuddy_config():
    return {
        "sites": {
            "workbuddy": {
                "enabled": True,
                "type": "workbuddy",
                "base_url": "https://www.codebuddy.cn",
                "accounts": [
                    {"name": "旧账号一", "token_env": "WB1_TOKEN"},
                    {"name": "旧账号二", "token_env": "WB2_TOKEN"},
                ],
            }
        }
    }


class WorkBuddyAccountConfigTests(unittest.TestCase):
    def test_workflow_maps_aggregate_secret(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-checkin.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "WORKBUDDY_ACCOUNTS_JSON: ${{ secrets.WORKBUDDY_ACCOUNTS_JSON }}",
            workflow,
        )

    def test_workflow_describes_workbuddy_stable_id_filter(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-checkin.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workbuddy:wb-7f3a8c91d2e4", workflow)

    def test_valid_aggregate_secret_supersedes_legacy_tokens(self):
        logger = Mock()
        with patch.dict(
            os.environ,
            {
                "WORKBUDDY_ACCOUNTS_JSON": (
                    '{"version":1,"accounts":[{"key":"wb-7f3a8c91d2e4",'
                    '"name":"个人号","access_token":"aggregate-token"}]}'
                ),
                "WB1_TOKEN": "legacy-token",
            },
            clear=True,
        ):
            site = Config(workbuddy_config(), logger).sites()[0]

        self.assertEqual(1, len(site.accounts))
        account = site.accounts[0]
        self.assertEqual("个人号", account.name)
        self.assertEqual("wb-7f3a8c91d2e4", account.stable_key)
        self.assertEqual("aggregate-token", account.token)
        self.assertIsNone(account.token_env)

    def test_valid_empty_aggregate_secret_disables_legacy_fallback(self):
        logger = Mock()
        with patch.dict(
            os.environ,
            {"WORKBUDDY_ACCOUNTS_JSON": '{"version":1,"accounts":[]}', "WB1_TOKEN": "legacy-token"},
            clear=True,
        ):
            self.assertEqual([], Config(workbuddy_config(), logger).sites())

    def test_invalid_aggregate_secret_falls_back_without_logging_secret(self):
        logger = Mock()
        secret = "invalid-secret-must-not-appear-in-logs"
        with patch.dict(
            os.environ,
            {"WORKBUDDY_ACCOUNTS_JSON": secret, "WB1_TOKEN": "legacy-token"},
            clear=True,
        ):
            site = Config(workbuddy_config(), logger).sites()[0]

        self.assertEqual(["旧账号一"], [account.name for account in site.accounts])
        self.assertNotIn(secret, str(logger.warning.call_args_list))

    def test_boolean_aggregate_version_falls_back_to_legacy_tokens(self):
        logger = Mock()
        with patch.dict(
            os.environ,
            {
                "WORKBUDDY_ACCOUNTS_JSON": '{"version":true,"accounts":[]}',
                "WB1_TOKEN": "legacy-token",
            },
            clear=True,
        ):
            sites = Config(workbuddy_config(), logger).sites()

        self.assertEqual(1, len(sites))
        self.assertEqual(["旧账号一"], [account.name for account in sites[0].accounts])

    def test_aggregate_rejects_duplicate_keys(self):
        logger = Mock()
        payload = (
            '{"version":1,"accounts":[{"key":"wb-7f3a8c91d2e4","name":"甲","access_token":"a"},'
            '{"key":"wb-7f3a8c91d2e4","name":"乙","access_token":"b"}]}'
        )
        with patch.dict(
            os.environ,
            {"WORKBUDDY_ACCOUNTS_JSON": payload, "WB2_TOKEN": "legacy-token"},
            clear=True,
        ):
            site = Config(workbuddy_config(), logger).sites()[0]

        self.assertEqual(["旧账号二"], [account.name for account in site.accounts])

    def test_aggregate_rejects_empty_tokens(self):
        logger = Mock()
        payload = (
            '{"version":1,"accounts":['
            '{"key":"wb-7f3a8c91d2e4","name":"甲","access_token":""}]}'
        )
        with patch.dict(
            os.environ,
            {"WORKBUDDY_ACCOUNTS_JSON": payload, "WB2_TOKEN": "legacy-token"},
            clear=True,
        ):
            site = Config(workbuddy_config(), logger).sites()[0]

        self.assertEqual(["旧账号二"], [account.name for account in site.accounts])

    def test_workbuddy_filter_uses_stable_key_and_notification_name(self):
        logger = Mock()
        with patch.dict(
            os.environ,
            {
                "WORKBUDDY_ACCOUNTS_JSON": (
                    '{"version":1,"accounts":[{"key":"wb-7f3a8c91d2e4",'
                    '"name":"个人号","access_token":"aggregate-token"}]}'
                )
            },
            clear=True,
        ):
            account = Config(workbuddy_config(), logger).sites()[0].accounts[0]

        self.assertEqual(("workbuddy", "wb-7f3a8c91d2e4"), parse_account_filter("workbuddy:wb-7f3a8c91d2e4"))
        self.assertTrue(account_matches_filter("workbuddy", account, "wb-7f3a8c91d2e4"))
        self.assertFalse(account_matches_filter("workbuddy", account, "个人号"))
        self.assertEqual("个人号", account.name)

    def test_workbuddy_cache_uses_stable_key_after_display_name_changes(self):
        account = Account(
            name="旧备注",
            stable_key="wb-7f3a8c91d2e4",
            token="aggregate-token",
        )
        site = Mock(key="workbuddy")
        store = Mock()
        store.get.return_value = {"token": "aggregate-token", "expires_at": None}
        store.is_expired.return_value = False
        signer = WorkBuddySigner(site, account, Mock(), store, Mock(), Mock())
        signer.checkin = Mock(return_value={"ok": True, "msg": "今日已签到"})

        signer.run()
        account.name = "新备注"
        signer.run()

        self.assertEqual(
            [
                call("workbuddy", "wb-7f3a8c91d2e4"),
                call("workbuddy", "wb-7f3a8c91d2e4"),
            ],
            store.get.call_args_list,
        )
        store.save.assert_not_called()

    def test_workbuddy_token_change_replaces_stable_key_cache(self):
        account = Account(
            name="个人号",
            stable_key="wb-7f3a8c91d2e4",
            token="new-token",
        )
        site = Mock(key="workbuddy")
        store = Mock()
        store.get.return_value = {"token": "old-token", "expires_at": None}
        store.is_expired.return_value = False
        signer = WorkBuddySigner(site, account, Mock(), store, Mock(), Mock())
        signer.checkin = Mock(return_value={"ok": True, "msg": "签到成功"})

        signer.run()

        store.get.assert_called_once_with("workbuddy", "wb-7f3a8c91d2e4")
        store.save.assert_called_once_with(
            "workbuddy",
            "wb-7f3a8c91d2e4",
            {"token": "new-token", "expires_at": None},
        )
        signer.checkin.assert_called_once_with(
            {"token": "new-token", "expires_at": None}
        )


if __name__ == "__main__":
    unittest.main()
