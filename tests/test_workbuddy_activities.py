import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from tempfile import TemporaryDirectory
from pathlib import Path

import requests

from signers.workbuddy_activities import ActivityRunner, parse_policy, task_available, save_activity_report


class FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        response = Mock(status_code=200)
        response.json.return_value = {"code": 0, "data": reply}
        return response


def policy(**changes):
    result = {"enabled": True, "compensation": False, "tasks": False,
              "travel": False, "expiryDays": 7}
    result.update(changes)
    return result


class ActivityTests(unittest.TestCase):
    def test_missing_policy_does_not_enable_writes(self):
        self.assertFalse(parse_policy("{}")["enabled"])
        self.assertFalse(parse_policy("broken")["enabled"])

    def test_policy_rejects_string_booleans_and_bad_expiry(self):
        with self.assertRaises(ValueError):
            parse_policy(json.dumps({"activities": {"enabled": "false"}}))
        with self.assertRaises(ValueError):
            parse_policy(json.dumps({"activities": {"expiryDays": True}}))

    def test_expired_locked_and_malformed_dates_are_not_accepted(self):
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.assertTrue(task_available({}, now))
        self.assertFalse(task_available({"locked": True}, now))
        self.assertFalse(task_available({"valid_end": "2026-10-01T00:00:00Z"}, now))
        self.assertFalse(task_available({"valid_start": "2027-01-01T00:00:00Z"}, now))
        self.assertFalse(task_available({"valid_end": "unknown"}, now))

    def test_disabled_runner_never_contacts_server(self):
        http = FakeHTTP([])
        result = ActivityRunner("secret", policy(enabled=False), http).run()
        self.assertTrue(result["ok"])
        self.assertEqual([], http.calls)

    def test_accept_requires_readback_and_claims_only_completed(self):
        tasks = [
            {"task_code": "new", "title": "新任务", "accept_status": "not_accepted"},
            {"task_code": "done", "title": "已完成", "accept_status": "completed"},
            {"task_code": "old", "title": "已领", "accept_status": "claimed"},
            {"task_code": "locked", "locked": True, "accept_status": "not_accepted"},
        ]
        accepted = [dict(t, accept_status="accepted") if t["task_code"] == "new" else t for t in tasks]
        claimed = [dict(t, accept_status="claimed") if t["task_code"] == "done" else t for t in accepted]
        http = FakeHTTP([{"tasks": tasks}, {"results": []}, {"tasks": accepted},
                         {"credit": 100}, {"tasks": claimed}])
        runner = ActivityRunner("secret", policy(tasks=True), http)
        runner.run_tasks()
        self.assertEqual(1, runner.result["accepted"])
        self.assertEqual(1, runner.result["claimed"])
        writes = [c for c in http.calls if c[0] == "POST"]
        self.assertEqual(2, len(writes))
        self.assertEqual({"task_codes": ["new"]}, writes[0][2]["json"])
        self.assertTrue(writes[1][1].endswith("/tasks/done/claim"))

    def test_accept_success_without_actual_registration_is_failure(self):
        tasks = [{"task_code": "new", "title": "新任务", "accept_status": "not_accepted"}]
        runner = ActivityRunner("secret", policy(), FakeHTTP([
            {"tasks": tasks}, {}, {"tasks": tasks}]))
        runner.run_tasks()
        self.assertEqual(0, runner.result["accepted"])
        self.assertTrue(runner.result["errors"])

    def test_travel_claim_timeout_never_departs_or_retries(self):
        http = FakeHTTP([{"state": "arrived", "record_id": "r1"}, requests.Timeout()])
        runner = ActivityRunner("secret", policy(), http)
        with self.assertRaises(Exception):
            runner.run_travel()
        self.assertEqual(2, len(http.calls))
        self.assertTrue(http.calls[-1][1].endswith("/travel/claim"))

    def test_travel_claim_must_read_back_before_departing(self):
        http = FakeHTTP([{"state": "arrived", "record_id": "r1"}, {"reward_credit": 30},
                         {"state": "arrived", "record_id": "r1"}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_travel()
        self.assertEqual(3, len(http.calls))
        self.assertEqual(0, runner.result["departed"])
        self.assertTrue(runner.result["errors"])

    def test_daily_travel_limit_does_not_send_depart(self):
        http = FakeHTTP([{"state": "idle", "daily_limit_reached": True}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_travel()
        self.assertEqual(1, len(http.calls))

    def test_travel_depart_reads_back_server_state(self):
        http = FakeHTTP([{"state": "idle"}, {"locations": [{"id": "city", "name": "海边"}]},
                         {}, {"state": "traveling", "arrive_at": 1800000000}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_travel()
        self.assertEqual(1, runner.result["departed"])
        self.assertEqual("traveling", runner.result["travel"]["state"])

    def test_missing_buddy_is_a_todo_without_invalid_accept_requests(self):
        tasks = [{"task_code": "first_buddy", "accept_status": "not_accepted"},
                 {"task_code": "new", "accept_status": "not_accepted"}]
        http = FakeHTTP([{"tasks": tasks}, {"buddy": None}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_tasks()
        self.assertEqual([], runner.result["errors"])
        self.assertTrue(runner.result["notices"])
        self.assertFalse(any(call[0] == "POST" for call in http.calls))

    def test_unowned_buddy_never_attempts_depart(self):
        http = FakeHTTP([{"state": "idle", "buddy_id": 0}, {"buddy": None}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_travel()
        self.assertEqual(2, len(http.calls))
        self.assertEqual("unowned", runner.result["travel"]["state"])
        self.assertEqual([], runner.result["errors"])

    def test_idle_zero_buddy_id_does_not_hide_owned_buddy(self):
        http = FakeHTTP([{"state": "idle", "buddy_id": 0},
                         {"buddy": {"instance_id": "owned"}},
                         {"locations": [{"id": "city", "name": "咖啡馆"}]},
                         {}, {"state": "traveling", "buddy_id": 123}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_travel()
        self.assertEqual(1, runner.result["departed"])
        self.assertEqual("traveling", runner.result["travel"]["state"])

    def test_unknown_buddy_info_never_sends_depart(self):
        http = FakeHTTP([{"state": "idle", "buddy_id": 0}, {}])
        runner = ActivityRunner("secret", policy(), http)
        with self.assertRaises(Exception):
            runner.run_travel()
        self.assertFalse(any(call[0] == "POST" for call in http.calls))

    def test_compensation_already_claimed_never_writes_claim(self):
        http = FakeHTTP([{"active": True, "claimed": True, "credit_num": 500}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_compensation()
        self.assertEqual(1, len(http.calls))
        self.assertNotIn("/v2/", http.calls[0][1])

    def test_compensation_claim_requires_confirmation(self):
        http = FakeHTTP([{"active": True, "claimed": False}, {"credit": 500},
                         {"active": True, "claimed": True}])
        runner = ActivityRunner("secret", policy(), http)
        runner.run_compensation()
        self.assertEqual(1, runner.result["compensation_claimed"])
        self.assertEqual(500, runner.result["credits"])

    def test_auth_failure_stops_other_stages_and_hides_token(self):
        http = FakeHTTP([{}])
        response = Mock(status_code=401)
        http.request = Mock(return_value=response)
        result = ActivityRunner("must-not-appear", policy(compensation=True, tasks=True, travel=True), http).run()
        self.assertFalse(result["ok"])
        self.assertEqual(1, http.request.call_count)
        self.assertNotIn("must-not-appear", json.dumps(result))

    def test_real_package_deduction_end_time_generates_expiry_notice(self):
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        http = FakeHTTP([{"Response": {"Data": {"Accounts": [
            {"Status": 0, "CapacityType": 1, "CapacityRemainPrecise": "2.96",
             "DeductionEndTime": int((now.timestamp() + 86400) * 1000)},
            {"Status": 0, "CapacityType": 4, "CapacityRemain": 500,
             "DeductionEndTime": int((now.timestamp() + 86400) * 1000)}]}}}])
        runner = ActivityRunner("secret", policy(), http, now)
        runner.expiry_notices()
        self.assertEqual(1, len(runner.result["notices"]))
        self.assertIn("2.96", runner.result["notices"][0])

    def test_unchanged_alert_is_not_notified_repeatedly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            results = [{"account": "账号", "activities": {"changed": False,
                       "errors": ["网络失败"], "notices": []}}]
            self.assertTrue(save_activity_report(results, path))
            self.assertFalse(save_activity_report(results, path))
            results[0]["activities"]["changed"] = True
            self.assertTrue(save_activity_report(results, path))

    def test_pre_activity_failure_still_triggers_poll_alert(self):
        with TemporaryDirectory() as directory:
            self.assertTrue(save_activity_report([{"site": "workbuddy", "account": "账号", "ok": False}],
                                                 Path(directory) / "report.json"))


if __name__ == "__main__":
    unittest.main()
