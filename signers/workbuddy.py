"""WorkBuddy 签到站（Playwright 驱动网页端）。

社区稳定方案是用 Playwright 操作界面（桌面端 .user-menu -> .daily-checkin-banner-action
「立即领取 ->」）。GitHub Actions 为无界面 Linux，这里改为驱动 WorkBuddy 的网页端：
自动登录 -> 点击用户菜单 -> 点击签到按钮。选择器均可在 config.yaml 中调整。
登录态（cookies）缓存复用，避免频繁登录。
"""
from .base import BaseSigner


class WorkBuddySigner(BaseSigner):
    type = "workbuddy"

    # WorkBuddy 整体走 Playwright，不走 requests，故 login/checkin 由 run() 内联实现
    def login(self):
        raise NotImplementedError

    def checkin(self, auth):
        raise NotImplementedError

    def run(self):
        from playwright.sync_api import sync_playwright

        raw = self.site.raw
        base = self.site.base_url.rstrip("/")
        login_cfg = raw.get("login", {})
        ck_cfg = raw.get("checkin", {})
        user_sel = login_cfg.get("user_selector", "input[type=text]")
        pass_sel = login_cfg.get("pass_selector", "input[type=password]")
        submit_sel = login_cfg.get("submit_selector", "button[type=submit]")
        menu_sel = ck_cfg.get("menu_selector", ".user-menu")
        btn_sel = ck_cfg.get("button_selector", ".daily-checkin-banner-action")
        btn_text = ck_cfg.get("button_text", "立即领取")

        result = {"ok": False, "points": 0, "msg": ""}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            auth = self.store.get(self.site_name, self.account.name)
            if auth and auth.get("cookies"):
                try:
                    ctx.add_cookies(auth["cookies"])
                except Exception:
                    pass
            page = ctx.new_page()
            try:
                self.logger.info(f"WorkBuddy/{self.account.name} 打开 {base}")
                page.goto(base, wait_until="domcontentloaded", timeout=60000)

                if self._need_login(page, user_sel):
                    self.logger.info(f"WorkBuddy/{self.account.name} 需要登录，正在填写凭证")
                    page.fill(user_sel, self.account.user)
                    page.fill(pass_sel, self.account.password)
                    page.click(submit_sel)
                    page.wait_for_timeout(5000)

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

                cookies = ctx.cookies()
                self.store.save(
                    self.site_name, self.account.name, {"cookies": cookies, "expires_at": None}
                )
            except Exception as e:
                result["ok"] = False
                result["msg"] = f"异常: {e}"
                self.logger.warning(f"WorkBuddy/{self.account.name}: {e}")
            finally:
                browser.close()
        return self._result(result)

    def _need_login(self, page, user_sel):
        try:
            page.wait_for_selector(user_sel, timeout=5000)
            return True
        except Exception:
            return False
