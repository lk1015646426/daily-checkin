import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from types import SimpleNamespace

from common.daily_summary import DailySummary
from common.notify import Notifier


class DailySummaryTests(unittest.TestCase):
    def test_poll_rewards_are_carried_into_one_full_summary_and_survive_reload(self):
        now = datetime(2026, 10, 21, 12, tzinfo=timezone(timedelta(hours=8)))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            manager = DailySummary(path, now)
            results = [{"site": "workbuddy", "account": "账号", "account_key": "stable",
                        "ok": True, "activities": {"enabled": True, "claimed": 2,
                        "credits": 200, "actions": ["任务奖励已领取"], "errors": [], "notices": []}}]
            manager.record(results)
            self.assertFalse(manager.should_send(activities_only=True))
            self.assertFalse(manager.should_send(account_filter=("workbuddy", "stable")))
            manager = DailySummary(path, now)
            latest = [{"site": "workbuddy", "account": "新备注", "account_key": "stable",
                       "ok": True, "activities": {"enabled": True, "claimed": 0,
                       "credits": 0, "actions": [], "errors": [], "notices": ["99积分即将到期"]}}]
            enriched = manager.record(latest)
            self.assertEqual(2, enriched[0]["activities"]["claimed"])
            self.assertEqual(200, enriched[0]["activities"]["credits"])
            self.assertTrue(manager.should_send())
            manager.mark_sent()
            self.assertFalse(DailySummary(path, now).should_send())
            self.assertTrue(DailySummary(path, now + timedelta(days=1)).should_send())
            self.assertEqual({}, json.loads(path.read_text())["pending"])

    def test_changed_expiry_amount_never_notifies_during_poll(self):
        with TemporaryDirectory() as directory:
            manager = DailySummary(Path(directory) / "summary.json")
            for amount in (100, 99, 100):
                manager.record([{"site": "workbuddy", "account": "账号", "ok": True,
                                 "activities": {"enabled": True, "notices": [str(amount)]}}])
                self.assertFalse(manager.should_send(activities_only=True))

    def test_failed_send_keeps_pending_rewards_and_does_not_mark_day(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            manager = DailySummary(path)
            manager.record([{"site": "workbuddy", "account": "账号", "activities":
                             {"enabled": True, "travel_claimed": 1, "credits": 8}}])
            # A failed or missing receipt never invokes mark_sent.
            reloaded = DailySummary(path)
            self.assertTrue(reloaded.should_send())
            self.assertEqual(8, next(iter(reloaded.state["pending"].values()))["credits"])


class NotificationReceiptTests(unittest.TestCase):
    @patch.dict("os.environ", {"TG_TOKEN": "secret", "TG_CHAT_ID": "test"})
    @patch("common.notify.requests.post")
    def test_telegram_rejection_is_not_reported_as_delivered(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"ok": False, "description": "secret"}
        notifier = Notifier({"channel": "telegram"}, Mock())
        self.assertFalse(notifier.send("summary"))
        self.assertNotIn("secret", str(notifier.logger.method_calls))

    @patch.dict("os.environ", {"TG_TOKEN": "secret", "TG_CHAT_ID": "test"})
    @patch("common.notify.requests.post")
    def test_telegram_success_requires_platform_receipt(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"ok": True}
        self.assertTrue(Notifier({"channel": "telegram"}, Mock()).send("summary"))

    @patch.dict("os.environ", {"SERVERCHAN_KEY": "secret"})
    @patch("common.notify.requests.post")
    def test_serverchan_business_failure_is_not_success(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"code": 40001}
        self.assertFalse(Notifier({"channel": "serverchan"}, Mock()).send("summary"))


class MainNotificationPolicyTests(unittest.TestCase):
    def run_main(self, path, activities_only="false", account_filter="", delivered=True):
        import main
        cfg = Mock()
        account = SimpleNamespace(name="账号", stable_key="wb-123456789abc")
        cfg.sites.return_value = [SimpleNamespace(key="workbuddy", type="workbuddy", accounts=[account])]
        signer = Mock()
        signer.run.return_value = {"site": "workbuddy", "account": "账号", "ok": True, "msg": "已签到"}
        with patch.dict("os.environ", {"CHECKIN_ACTIVITIES_ONLY": activities_only,
                                      "CHECKIN_ACCOUNT_FILTER": account_filter}), \
                patch("main.Config.load", return_value=cfg), \
                patch("main.parse_policy", return_value={"enabled": False}), \
                patch.dict(main.SIGNERS, {"workbuddy": Mock(return_value=signer)}), \
                patch("main.setup_logger", return_value=Mock()), \
                patch("main.TokenStore"), patch("main.create_session"), \
                patch("main.save_activity_report"), \
                patch("main.DailySummary", side_effect=lambda: DailySummary(path)), \
                patch("common.notify.Notifier.send", return_value=delivered) as send:
            with self.assertRaises(SystemExit) as outcome:
                main.main()
            return send.call_count, outcome.exception.code

    def test_poll_and_single_account_validation_do_not_send(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            self.assertEqual((0, 0), self.run_main(path, activities_only="true"))
            self.assertEqual((0, 0), self.run_main(path, account_filter="workbuddy:wb-123456789abc"))

    def test_two_full_runs_on_one_day_send_only_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            self.assertEqual((1, 0), self.run_main(path))
            self.assertEqual((0, 0), self.run_main(path))

    def test_rejected_receipt_does_not_consume_daily_notification(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            self.assertEqual((1, 1), self.run_main(path, delivered=False))
            self.assertEqual((1, 0), self.run_main(path))


if __name__ == "__main__":
    unittest.main()
