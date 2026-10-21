"""每日完整通知及活动累计；定时轮询只记账，不推送。状态随 store 缓存持久化。"""
import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


COUNTERS = ("accepted", "claimed", "compensation_claimed", "travel_claimed", "departed", "credits")
TZ = timezone(timedelta(hours=8))


class DailySummary:
    def __init__(self, path="store/daily_summary.json", now=None):
        self.path = Path(path)
        self.today = (now or datetime.now(TZ)).astimezone(TZ).strftime("%Y-%m-%d")
        self.state = {"last_sent_day": None, "pending": {}}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.state, dict) or not isinstance(self.state.get("pending"), dict):
                raise ValueError("每日汇总缓存结构无效，请检查运行记录")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".daily-summary-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, indent=2)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def record(self, results):
        enriched = copy.deepcopy(results)
        for result in enriched:
            activity = result.get("activities")
            if not activity or not activity.get("enabled"):
                continue
            key = f"{result.get('site', 'workbuddy')}:{result.get('account_key') or result['account']}"
            pending = self.state["pending"].setdefault(key, {name: 0 for name in COUNTERS})
            for name in COUNTERS:
                pending[name] = pending.get(name, 0) + activity.get(name, 0)
                activity[name] = pending[name]
            actions = pending.setdefault("actions", [])
            for action in activity.get("actions", []):
                if action not in actions:
                    actions.append(action)
            pending["actions"] = actions[-30:]
            activity["actions"] = list(pending["actions"])
        self.save()
        return enriched

    def should_send(self, activities_only=False, account_filter=None):
        return not activities_only and not account_filter and self.state.get("last_sent_day") != self.today

    def mark_sent(self):
        # 仅在通知平台确认接收之后调用，失败时累计奖励留待下次完整汇总。
        self.state["last_sent_day"] = self.today
        self.state["pending"] = {}
        self.save()
