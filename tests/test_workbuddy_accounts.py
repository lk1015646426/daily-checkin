import os
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from common.config import Account, Config
from main import account_matches_filter, parse_account_filter
from signers.workbuddy import WorkBuddySigner, parse_available_credits
from common.notify import Notifier


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
    def test_activity_config_keeps_existing_account_auth_schema(self):
        with patch.dict(os.environ, {"WORKBUDDY_ACCOUNTS_JSON": (
            '{"version":1,"accounts":[{"key":"wb-7f3a8c91d2e4","name":"个人号",'
            '"access_token":"aggregate-token"}],"activities":{"enabled":true}}'
        )}, clear=True):
            site = Config(workbuddy_config(), Mock()).sites()[0]
        self.assertEqual("aggregate-token", site.accounts[0].token)
        self.assertEqual("wb-7f3a8c91d2e4", site.accounts[0].stable_key)

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


class WorkBuddyCreditsTests(unittest.TestCase):
    def test_official_summary_includes_trial_and_addons_once(self):
        payload = {"code": 0, "data": {"Packages": [
            {"CycleRemainCapacity": "3500"}, {"CycleRemainCapacity": "500"},
            {"CycleRemainCapacity": "1000"}],
            "TotalCredits": "5000", "CreditGroups": [{"CycleRemainCapacity": "5000"}]}}
        self.assertEqual(5000, parse_available_credits(payload))

    def test_summary_uses_current_remaining_not_total_and_preserves_precision(self):
        payload = {"code": 0, "data": {"Packages": [
            {"CycleRemainCapacity": "2932.12000031", "CycleTotalCapacity": "3400"},
            {"CycleRemainCapacity": "500", "CycleTotalCapacity": "500"}]}}
        self.assertEqual(3432.12, parse_available_credits(payload))

    def test_zero_and_unknown_are_different(self):
        self.assertEqual(0, parse_available_credits({"code": 0, "data": {"Packages": []}}))
        self.assertEqual(0, parse_available_credits({"code": 0, "data": {"Packages": [
            {"CycleRemainCapacity": "0", "CycleTotalCapacity": "500"}]}}))
        for payload in [{}, {"code": 401}, {"code": 0, "data": {}},
                        {"code": 0, "data": {"Packages": [{"CycleRemainCapacity": True}]}},
                        {"code": 0, "data": {"Packages": [{"CycleRemainCapacity": "NaN"}]}}]:
            self.assertIsNone(parse_available_credits(payload))

    def test_query_failure_does_not_invent_zero_balance(self):
        session = Mock()
        session.post.side_effect = TimeoutError()
        signer = WorkBuddySigner(Mock(key="workbuddy"), Account(name="测试"), session, Mock(), Mock(), Mock())
        self.assertIsNone(signer._get_total_credits({}))

    def test_rewards_finish_before_balance_is_queried(self):
        from main import run_workbuddy_activities
        events = []
        signer = Mock()
        signer.login.return_value = {"token": "secret"}
        signer._get_total_credits.side_effect = lambda headers: events.append("balance") or 600
        activity = {"ok": True, "summary": "已领奖", "enabled": True}
        with patch("main.ActivityRunner") as runner:
            runner.return_value.run.side_effect = lambda: events.append("activity") or activity
            result = {"points": 500}
            run_workbuddy_activities(signer, result, {"enabled": True})
        self.assertEqual(["activity", "balance"], events)
        self.assertEqual(600, result["points"])

    def test_unknown_balance_is_explicit_in_notification(self):
        text = Notifier.format_summary([{"site": "workbuddy", "account": "测试", "ok": True,
            "points": None, "points_unit": "积分", "awarded": 100}])
        self.assertIn("余额未查询到", text)
        self.assertNotIn("余额 **0", text)

    def test_post_activity_balance_failure_is_reported_as_partial_failure(self):
        from main import run_workbuddy_activities
        signer = Mock()
        signer.login.return_value = {"token": "secret"}
        signer._get_total_credits.return_value = None
        activity = {"ok": True, "summary": "活动检查完成", "enabled": True, "errors": []}
        with patch("main.ActivityRunner") as runner:
            runner.return_value.run.return_value = activity
            result = {}
            run_workbuddy_activities(signer, result, {"enabled": True})
        self.assertIsNone(result["points"])
        self.assertFalse(result["activities"]["ok"])
        self.assertIn("余额未查询到", result["activity_summary"])


if __name__ == "__main__":
    unittest.main()
