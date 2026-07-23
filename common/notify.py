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
                self._tg(text)
            elif self.channel == "serverchan":
                self._sc(text)
            else:
                self.logger.info("通知渠道为 none，跳过推送")
        except Exception as e:
            self.logger.warning(f"通知发送失败: {e}")

    def _tg(self, text):
        tg = self.cfg.get("telegram", {})
        token = os.environ.get(tg.get("token_env", "TG_TOKEN"))
        chat = os.environ.get(tg.get("chat_id_env", "TG_CHAT_ID"))
        if not token or not chat:
            self.logger.warning("Telegram 凭证缺失，跳过推送")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(
            url, json={"chat_id": chat, "text": text, "parse_mode": "Markdown"}, timeout=15
        )

    def _sc(self, text):
        key = os.environ.get(self.cfg.get("serverchan", {}).get("key_env", "SERVERCHAN_KEY"))
        if not key:
            self.logger.warning("Server酱 key 缺失，跳过推送")
            return
        url = f"https://sctapi.ftqq.com/{key}.send"
        requests.post(url, data={"title": "每日签到结果", "desp": text}, timeout=15)

    @staticmethod
    def format_summary(results):
        lines = ["📅 *每日签到*"]
        for r in results:
            status = "✅" if r.get("ok") else "❌"
            # 站点显示名：workbuddy -> WorkBuddy
            site = r["site"]
            site_display = "WorkBuddy" if site == "workbuddy" else site
            name = f"{site_display} {r['account']}"

            if not r.get("ok"):
                lines.append(f"{status} {name}：{r.get('msg', '失败')}")
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
            if isinstance(pts, (int, float)) and pts is not None:
                if unit == "USD":
                    parts.append(f"总额度 **${pts:.2f}**")
                elif unit == "积分":
                    pts_str = f"{pts:g}" if isinstance(pts, float) else str(pts)
                    parts.append(f"余额 **{pts_str}积分**")

            # 连续天数
            if streak and streak > 0:
                parts.append(f"连续{streak}天")

            if parts:
                lines.append(f"{status} {name}：" + " | ".join(parts))
            else:
                lines.append(f"{status} {name}：{r.get('msg', '')}")

        # Server酱/Markdown 渲染需要双换行才能分段显示
        return "\n\n".join(lines)
