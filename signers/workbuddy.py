"""WorkBuddy 签到站（Playwright 驱动网页端）。

WorkBuddy 使用手机号+验证码 / 微信扫码登录，CI 无法自动完成登录，
因此本签到站不尝试账号密码登录，而是直接使用本地导出的登录态 cookies
（存于 GitHub Secrets 的 WB1_COOKIES / WB2_COOKIES，由
scripts/capture_workbuddy_token.py 在本机手动登录后生成）。
cookies 注入后打开签到页 -> 点击用户菜单 -> 点击签到按钮。
选择器均可在 config.yaml 的 checkin 中调整。
"""
from .base import BaseSigner


class WorkBuddySigner(BaseSigner):
    type = "workbuddy"

    def login(self):
        raise NotImplementedError

    def checkin(self, auth):
        raise NotImplementedError

    def run(self):
        import json
        import os
        from playwright.sync_api import sync_playwright

        raw = self.site.raw
        base = self.site.base_url.rstrip("/")
        ck_cfg = raw.get("checkin", {})
        menu_sel = ck_cfg.get("menu_selector", ".user-menu")
        btn_sel = ck_cfg.get("button_selector", ".daily-checkin-banner-action")
        btn_text = ck_cfg.get("button_text", "立即领取")

        result = {"ok": False, "points": 0, "msg": ""}

        # 读取本账号的登录态 cookies：优先本地文件（capture 脚本自动保存），
        # 其次环境变量（CI/Secrets）
        cookies_env = self.account.cookies_env
        cookies = None
        cookie_file = os.path.join("store", f"wb_cookies_{self.account.name}.json")
        if os.path.exists(cookie_file):
            try:
                with open(cookie_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
            except Exception as e:
                self.logger.warning(f"读取 {cookie_file} 失败: {e}")
        if not cookies and cookies_env:
            raw_cookies = os.environ.get(cookies_env)
            if raw_cookies:
                try:
                    cookies = json.loads(raw_cookies)
                except Exception as e:
                    self.logger.warning(f"解析 {cookies_env} 失败: {e}")
        if not cookies:
            result["msg"] = (
                f"账号 {self.account.name} 缺少登录态：请在本机运行 "
                f"python scripts/capture_workbuddy_token.py 取 cookies，"
                f"并填入 Secrets {cookies_env}"
            )
            return self._result(result)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            try:
                ctx.add_cookies(cookies)
            except Exception as e:
                self.logger.warning(f"注入 cookies 失败（可能已失效）: {e}")
            page = ctx.new_page()
            try:
                self.logger.info(f"WorkBuddy/{self.account.name} 打开 {base}")
                page.goto(base, wait_until="domcontentloaded", timeout=60000)

                if self._need_login(page):
                    result["ok"] = False
                    result["msg"] = (
                        f"cookies 已失效，请重新运行 capture 脚本并更新 Secrets {cookies_env}"
                    )
                    return self._result(result)

                self.logger.info(f"WorkBuddy/{self.account.name} 点击用户菜单")
                page.click(menu_sel, timeout=15000)
                page.wait_for_timeout(2000)

                self.logger.info(f"WorkBuddy/{self.account.name} 点击签到按钮")
                try:
                    page.click(btn_sel, timeout=10000)
                except Exception:
                    page.get_by_text(btn_text, exact=False).first.click(timeout=10000)
                page.wait_for_timeout(3000)

                result["ok"] = True
                result["msg"] = "已点击签到"
                try:
                    body = page.inner_text("body") or ""
                    if any(k in body for k in ("已签到", "成功", "领取成功", "签到成功")):
                        result["msg"] = "签到成功（页面提示）"
                except Exception:
                    pass
            except Exception as e:
                result["ok"] = False
                result["msg"] = f"异常: {e}"
                self.logger.warning(f"WorkBuddy/{self.account.name}: {e}")
            finally:
                browser.close()
        return self._result(result)

    def _need_login(self, page):
        for sel in (
            "input[type=password]",
            "button:has-text('登录')",
            "input[type=tel]",
            "input[placeholder*=验证码]",
        ):
            try:
                page.wait_for_selector(sel, timeout=4000)
                return True
            except Exception:
                continue
        return False
