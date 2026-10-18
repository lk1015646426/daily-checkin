"""WorkBuddy 活动：只做接取、领取和旅行，不生成使用事件或消耗能量。

接口契约参考（实现独立编写）：
https://github.com/88lin/workbuddy-auto-signin
https://github.com/ithtelab/workbuddy-manager
写请求不自动重试；执行后必须回读，不能用 HTTP 200 代替实际完成。
"""
import json
import hashlib
import math
import os
import re
import time
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from common.session import DEFAULT_UA

BASE = "https://copilot.tencent.com"
GROWTH = BASE + "/v2/activity/growth"
BILLING = "https://www.codebuddy.cn/billing/meter"
DEFAULT_POLICY = {"enabled": False, "compensation": True, "tasks": True,
                  "travel": True, "expiryDays": 7}
TZ = timezone(timedelta(hours=8))
TASK_CODE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def parse_policy(raw=None):
    try:
        aggregate = json.loads(raw if raw is not None else os.environ.get("WORKBUDDY_ACCOUNTS_JSON", "{}"))
    except (ValueError, TypeError):
        return dict(DEFAULT_POLICY)
    value = aggregate.get("activities", {}) if isinstance(aggregate, dict) else {}
    if not isinstance(value, dict):
        raise ValueError("活动配置结构无效")
    policy = dict(DEFAULT_POLICY)
    for key in ("enabled", "compensation", "tasks", "travel"):
        if key in value:
            if type(value[key]) is not bool:
                raise ValueError("活动开关必须为布尔值")
            policy[key] = value[key]
    if "expiryDays" in value:
        days = value["expiryDays"]
        if type(days) is not int or not 1 <= days <= 30:
            raise ValueError("到期提醒天数必须为 1 到 30 的整数")
        policy["expiryDays"] = days
    return policy


def clean(value, limit=100):
    return "".join(c for c in str(value or "") if c.isprintable())[:limit]


def date_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000 if value > 10**10 else value, TZ)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)


def task_available(task, now):
    if task.get("locked"):
        return False
    try:
        start, end = date_value(task.get("valid_start")), date_value(task.get("valid_end"))
        return not ((start and now < start) or (end and now > end))
    except (ValueError, TypeError, OverflowError, OSError):
        return False


class ActivityError(Exception):
    def __init__(self, message, auth=False):
        super().__init__(message)
        self.auth = auth


class ActivityRunner:
    def __init__(self, token, policy, session=None, now=None):
        self.policy = policy
        self.session = session if session is not None else requests.Session()
        self.owns_session = session is None
        self.headers = {"Authorization": f"Bearer {token}", "User-Agent": DEFAULT_UA,
                        "Accept": "application/json", "Content-Type": "application/json",
                        "Origin": "https://www.codebuddy.cn", "Referer": "https://www.codebuddy.cn/"}
        self.now = now or datetime.now(TZ)
        self.deadline = time.monotonic() + 100
        self.result = {"ok": True, "enabled": policy["enabled"], "accepted": 0, "claimed": 0,
                       "compensation_claimed": 0, "travel_claimed": 0, "departed": 0,
                       "credits": 0, "changed": False, "tasks": [], "travel": {},
                       "notices": [], "errors": [], "actions": [],
                       "checked_at": self.now.isoformat()}

    def request(self, method, url, payload=None):
        if time.monotonic() > self.deadline:
            raise ActivityError("本轮时间预算已用完，剩余项目留待下次检查")
        try:
            kwargs = {"headers": self.headers, "timeout": 15, "allow_redirects": False}
            if method == "POST":
                kwargs["json"] = payload or {}
            response = self.session.request(method, url, **kwargs)
        except requests.RequestException as error:
            # 不拼接异常原文、响应原文或请求头，避免认证信息进入日志。
            raise ActivityError(f"请求未获确认（{type(error).__name__}），本轮不重试写操作") from None
        if response.status_code == 401:
            raise ActivityError("登录凭证被拒绝，请检查桌面端同步状态", auth=True)
        if not 200 <= response.status_code < 300:
            raise ActivityError(f"接口返回 HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise ActivityError("接口返回的数据无法解析") from None
        if not isinstance(body, dict) or body.get("code") != 0:
            code = body.get("code") if isinstance(body, dict) else None
            safe_code = str(code) if type(code) is int else "未知"
            raise ActivityError(f"接口业务未成功（代码 {safe_code}）")
        data = body.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ActivityError("接口数据结构不符合预期")
        return data

    def error(self, message):
        self.result["errors"].append(clean(message, 180))

    def add_credits(self, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            self.result["credits"] += value

    def run_compensation(self):
        state = self.request("POST", BILLING + "/compensation-status")
        if state.get("claimed"):
            self.result["compensation"] = "已领取"
            return
        if state.get("active") is False:
            self.result["compensation"] = "暂无可领补偿"
            return
        if state.get("active") is not True:
            raise ActivityError("补偿状态无法确认，未尝试领取")
        receipt = self.request("POST", BILLING + "/claim-compensation")
        confirmed = self.request("POST", BILLING + "/compensation-status")
        if confirmed.get("claimed") is not True:
            raise ActivityError("补偿领取回执未被状态查询确认")
        self.result["compensation_claimed"] += 1
        self.add_credits(receipt.get("credit"))
        self.result["actions"].append("活动补偿已领取")
        self.result["compensation"] = "本次领取成功"

    def task_list(self):
        data = self.request("GET", GROWTH + "/tasks")
        if not isinstance(data.get("tasks"), list) or not all(isinstance(t, dict) for t in data["tasks"]):
            raise ActivityError("任务列表结构无效")
        return data["tasks"]

    def run_tasks(self):
        tasks = self.task_list()
        first_buddy = next((task for task in tasks if task.get("task_code") == "first_buddy"), None)
        missing_buddy = False
        if first_buddy is not None and first_buddy.get("accept_status") != "claimed":
            buddy = self.request("GET", GROWTH + "/buddy/info")
            if "buddy" not in buddy:
                raise ActivityError("Buddy 信息无法识别，未自动接取任务")
            missing_buddy = buddy["buddy"] is None
            if missing_buddy:
                self.result["notices"].append("请先在官方成长中心领取一只 Buddy，再接取其余成长任务")
        pending = [t for t in tasks if t.get("accept_status") == "not_accepted"
                   and not missing_buddy and task_available(t, self.now)
                   and TASK_CODE.fullmatch(str(t.get("task_code", "")))]
        for offset in range(0, len(pending), 20):
            batch = pending[offset:offset + 20]
            codes = [t["task_code"] for t in batch]
            self.request("POST", GROWTH + "/tasks/accept", {"task_codes": codes})
            tasks = self.task_list()
            states = {t.get("task_code"): t.get("accept_status") for t in tasks}
            for task in batch:
                if states.get(task["task_code"]) in ("accepted", "in_progress", "completed", "claimed"):
                    self.result["accepted"] += 1
                else:
                    self.error(f"任务「{clean(task.get('title'))}」接取尚未确认，可能缺少前置条件")
        completed = [t for t in tasks if t.get("accept_status") == "completed"
                     and task_available(t, self.now) and TASK_CODE.fullmatch(str(t.get("task_code", "")))]
        for task in completed:
            receipt = self.request("POST", GROWTH + f"/tasks/{task['task_code']}/claim")
            tasks = self.task_list()
            confirmed = any(t.get("task_code") == task["task_code"] and t.get("accept_status") == "claimed" for t in tasks)
            if confirmed:
                self.result["claimed"] += 1
                self.add_credits(receipt.get("credit", receipt.get("reward_credit")))
                self.result["actions"].append(f"任务「{clean(task.get('title'))}」奖励已领取")
            else:
                self.error(f"任务「{clean(task.get('title'))}」领奖未被回读确认")
        if self.result["accepted"]:
            self.result["actions"].append(f"新接取 {self.result['accepted']} 项任务，等待实际使用完成")
        self.record_tasks(tasks)

    def record_tasks(self, tasks):
        for task in tasks:
            title = clean(task.get("title") or task.get("task_code"))
            state = clean(task.get("accept_status"))
            self.result["tasks"].append({"title": title, "state": state,
                                         "code": clean(task.get("task_code")),
                                         "available": task_available(task, self.now),
                                         "valid_end": clean(task.get("valid_end"))})
            if state == "claimed":
                continue
            try:
                end = date_value(task.get("valid_end"))
                if end and end <= self.now + timedelta(days=self.policy["expiryDays"]):
                    self.result["notices"].append(f"任务「{title}」{'已结束' if end < self.now else '即将结束'}：{end.astimezone(TZ):%m月%d日}")
            except (ValueError, TypeError, OverflowError, OSError):
                self.result["notices"].append(f"任务「{title}」有效期无法识别，未自动操作")

    def travel_status(self):
        state = self.request("GET", GROWTH + "/buddy/travel/status")
        if state.get("state") not in ("idle", "traveling", "arrived"):
            raise ActivityError("旅行状态无法识别，请先在官方成长中心确认 Buddy 已领取")
        self.result["travel"] = {k: state[k] for k in ("state", "daily_limit_reached", "arrive_at", "server_now") if k in state}
        return state

    def run_travel(self):
        state = self.travel_status()
        if state.get("buddy_id") == 0:
            self.result["travel"]["state"] = "unowned"
            self.result["notices"].append("尚未领取 Buddy，旅行已跳过")
            return
        if state["state"] == "arrived":
            record_id = state.get("record_id")
            if not isinstance(record_id, (str, int)) or isinstance(record_id, bool) or not str(record_id):
                raise ActivityError("旅行领取记录缺失，未尝试领取")
            receipt = self.request("POST", GROWTH + "/buddy/travel/claim", {"record_id": record_id})
            state = self.travel_status()
            if state["state"] == "arrived":
                self.error("旅行礼物领取未被状态查询确认，未派出下一次旅行")
                return
            self.result["travel_claimed"] += 1
            self.add_credits(receipt.get("reward_credit"))
            self.result["actions"].append("旅行礼物已领取")
        if state["state"] != "idle" or state.get("daily_limit_reached"):
            return
        config = self.request("GET", GROWTH + "/buddy/travel/config")
        locations = config.get("locations")
        if not isinstance(locations, list) or not locations:
            raise ActivityError("当前没有可用旅行目的地")
        # 只选服务端提供的目的地；不调用盲盒、抽奖或能量消耗接口。
        location = next((p for p in locations if isinstance(p, dict) and p.get("id") is not None
                         and not p.get("locked") and not p.get("disabled")), None)
        if location is None:
            raise ActivityError("旅行目的地尚未解锁")
        self.request("POST", GROWTH + "/buddy/travel/depart", {"location_id": location["id"]})
        confirmed = self.travel_status()
        if confirmed["state"] != "traveling":
            raise ActivityError("旅行派出未被状态查询确认")
        self.result["departed"] += 1
        self.result["actions"].append(f"Buddy 已出发：{clean(location.get('name') or '旅行目的地')}")

    def expiry_notices(self):
        data = self.request("POST", BASE + "/v2/billing/meter/get-user-resource")
        packages = data.get("Response", {}).get("Data", {}).get("Accounts")
        if not isinstance(packages, list):
            raise ActivityError("积分包列表结构无效")
        for package in packages:
            if not isinstance(package, dict) or package.get("Status") != 0 or package.get("CapacityType") == 4:
                continue
            try:
                remain = float(package.get("CapacityRemainPrecise") or package.get("CapacityRemain") or 0)
                end = date_value(package.get("DeductionEndTime") or package.get("ExpiredTime")
                                 or package.get("ExpireTime") or package.get("EndTime")
                                 or package.get("CapacityExpireTime") or package.get("CycleEndTime"))
                if remain > 0 and end and self.now <= end <= self.now + timedelta(days=self.policy["expiryDays"]):
                    self.result["notices"].append(f"{remain:g} 积分将于 {end.astimezone(TZ):%m月%d日} 到期")
            except (ValueError, TypeError, OverflowError, OSError):
                self.result["notices"].append("部分积分包的到期时间无法识别")

    def run(self):
        if not self.policy["enabled"]:
            return self.result
        try:
            for label, enabled, action in (("补偿", self.policy["compensation"], self.run_compensation),
                                           ("任务", self.policy["tasks"], self.run_tasks),
                                           ("旅行", self.policy["travel"], self.run_travel),
                                           ("到期查询", True, self.expiry_notices)):
                if not enabled:
                    continue
                try:
                    action()
                except ActivityError as error:
                    self.error(f"{label}：{error}")
                    if error.auth:
                        break
                except Exception:
                    self.error(f"{label}：返回结构异常，本轮已停止该项目")
            self.result["ok"] = not self.result["errors"]
            self.result["changed"] = bool(self.result["actions"])
            self.result["summary"] = format_activity(self.result)
            return self.result
        finally:
            if self.owns_session:
                self.session.close()


def format_activity(result):
    if not result.get("enabled"):
        return ""
    lines = [f"活动：接取 {result['accepted']} 项任务，领奖 {result['claimed']} 项；补偿 {result['compensation_claimed']} 次，旅行收礼 {result['travel_claimed']} 次、派出 {result['departed']} 次"]
    if result.get("credits"):
        lines.append(f"本轮活动回执确认 +{result['credits']:g} 积分")
    lines.extend(result.get("actions", [])[-6:])
    lines.extend("提醒：" + v for v in result.get("notices", [])[:6])
    lines.extend("⚠️ " + v for v in result.get("errors", [])[:6])
    return "\n".join(lines)


def save_activity_report(results, filename):
    """存储不含凭证的活动报告；相同异常/到期提醒一天只触发一次轮询通知。"""
    path = Path(filename)
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    activities = []
    for result in results:
        if result.get("activities"):
            activities.append({"account": result["account"], **result["activities"]})
        elif result.get("site") == "workbuddy" and not result.get("ok"):
            activities.append({"account": result["account"], "changed": False,
                               "errors": ["活动执行前失败，请检查账号凭证和运行日志"], "notices": []})
    if not activities:
        return False
    alerts = [{"account": r["account"], "errors": r.get("errors", []), "notices": r.get("notices", [])}
              for r in activities if r.get("errors") or r.get("notices")]
    digest = hashlib.sha256(json.dumps(alerts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    changed = any(r.get("changed") for r in activities)
    should_notify = changed or bool(alerts and (previous.get("alert_digest") != digest or previous.get("day") != today))
    report = {"day": today, "alert_digest": digest, "accounts": activities}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".activities-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return should_notify
