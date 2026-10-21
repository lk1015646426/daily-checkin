"""结果通知：Telegram / Server酱 / none。"""
import os

import requests


class Notifier:
    def __init__(self, cfg, logger):
        self.cfg = cfg or {}
        self.channel = (self.cfg.get("channel") or "none").lower()
        self.logger = logger

    def send(self, text):
        try:
            if self.channel == "telegram":
                return self._tg(text)
            elif self.channel == "serverchan":
                return self._sc(text)
            else:
                self.logger.info("通知渠道为 none，跳过推送")
                return True
        except Exception as e:
            # 注意：异常消息会包含完整请求 URL（TG bot token / Server酱 key 在 URL 中），
            # 不能直接记录 {e}，否则密钥会写入日志。只记录异常类型。
            self.logger.warning(
                f"通知发送失败 ({self.channel}): {type(e).__name__}"
            )
            return False

    def _tg(self, text):
        tg = self.cfg.get("telegram", {})
        token = os.environ.get(tg.get("token_env", "TG_TOKEN"))
        chat = os.environ.get(tg.get("chat_id_env", "TG_CHAT_ID"))
        if not token or not chat:
            self.logger.warning("Telegram 凭证缺失，跳过推送")
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        # 纯文本避免账号备注中的 Markdown 字符导致整份汇总被拒绝。
        plain = text.replace("**", "").replace("*每日签到*", "每日签到")
        response = requests.post(url, json={"chat_id": chat, "text": plain}, timeout=15)
        if response.status_code != 200 or response.json().get("ok") is not True:
            raise RuntimeError("通知平台未确认接收")
        self.logger.info("Telegram 已确认接收每日汇总")
        return True

    def _sc(self, text):
        key = os.environ.get(self.cfg.get("serverchan", {}).get("key_env", "SERVERCHAN_KEY"))
        if not key:
            self.logger.warning("Server酱 key 缺失，跳过推送")
            return False
        url = f"https://sctapi.ftqq.com/{key}.send"
        response = requests.post(url, data={"title": "每日签到结果", "desp": text}, timeout=15)
        body = response.json()
        if response.status_code != 200 or type(body.get("code")) is not int or body["code"] != 0:
            raise RuntimeError("通知平台未确认接收")
        self.logger.info("Server酱已确认接收每日汇总")
        return True

    @staticmethod
    def format_summary(results):
        lines = ["📅 *每日签到*"]
        for r in results:
            status = "✅" if r.get("ok") else "❌"
            # 站点显示名：workbuddy -> WorkBuddy
            site = r["site"]
            site_display = {
                "workbuddy": "WorkBuddy",
                "trae": "TRAE",
            }.get(site, site)
            name = f"{site_display} {r['account']}"

            if not r.get("ok"):
                lines.append(f"{status} {name}：{r.get('msg', '失败')}")
                if r.get("activity_summary"):
                    lines.append(r["activity_summary"])
                continue

            unit = r.get("points_unit", "")
            pts = r.get("points")
            awarded = r.get("awarded")
            streak = r.get("streak")
            parts = []

            # 今日获得
            if awarded and awarded > 0:
                if unit == "USD":
                    parts.append(f"+${awarded:.2f}")
                else:
                    parts.append(f"+{awarded}积分")

            # 余额/总额度（加粗醒目）
            if pts is None and unit == "积分":
                parts.append("余额未查询到")
            if isinstance(pts, (int, float)) and pts is not None:
                if unit == "USD":
                    parts.append(f"总额度 **${pts:.2f}**")
                elif unit == "积分":
                    if isinstance(pts, float) and (pts == float("inf") or pts == float("-inf")):
                        pts_str = "无限"
                    else:
                        pts_str = f"{pts:g}" if isinstance(pts, float) else str(pts)
                    parts.append(f"余额 **{pts_str}积分**")

            # 连续天数
            if streak and streak > 0:
                parts.append(f"连续{streak}天")

            if parts:
                lines.append(f"{status} {name}：" + " | ".join(parts))
            else:
                lines.append(f"{status} {name}：{r.get('msg', '')}")
            if r.get("activity_summary"):
                lines.append(r["activity_summary"])

        # Server酱/Markdown 渲染需要双换行才能分段显示
        return "\n\n".join(lines)
