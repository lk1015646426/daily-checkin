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
        lines = ["📅 *每日签到结果*"]
        for r in results:
            status = "✅" if r.get("ok") else "❌"
            pts = r.get("points")
            unit = r.get("points_unit", "")
            if isinstance(pts, (int, float)) and pts:
                if unit == "USD":
                    pts_s = f" +${pts:.2f}"
                elif unit == "积分":
                    pts_s = f" {pts}{unit}"
                else:
                    pts_s = f" +{pts}"
            else:
                pts_s = ""
            cached = " (缓存复用)" if r.get("cached") else ""
            lines.append(f"{status} {r['site']}/{r['account']}{pts_s}: {r.get('msg', '')}{cached}")
        return "\n".join(lines)
