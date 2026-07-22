"""本地一次性工具：用 Playwright 登录 WorkBuddy 并把 cookies 写入 store/ 缓存。

适用场景：
- WorkBuddy 登录需要验证码/设备绑定时，CI 无头浏览器无法自动通过。
- 本地以「有界面」模式运行本脚本，手动完成验证码，脚本会自动保存登录态，
  之后 GitHub Actions 上的日常签到即可直接复用缓存 cookies，无需再登录。

用法：
  pip install -r requirements.txt
  playwright install chromium
  # 在 .env 中填好 WB1_*/WB2_* 后：
  CAPTURE_HEADFUL=1 python scripts/capture_workbuddy_token.py
（CAPTURE_HEADFUL=1 会以可见浏览器运行，便于手动过验证码；留空则无头运行）
"""
import os
import sys

from common.config import Config
from common.logger import setup_logger
from common.store import TokenStore


def main():
    logger = setup_logger("capture")
    cfg = Config.load("config.yaml", logger)
    store = TokenStore("store/tokens.json")

    wb_sites = [s for s in cfg.sites() if s.type == "workbuddy"]
    if not wb_sites:
        logger.error("config.yaml 中未启用 workbuddy 站点")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    headful = bool(os.environ.get("CAPTURE_HEADFUL"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        ctx = browser.new_context()
        for site in wb_sites:
            raw = site.raw
            base = site.base_url.rstrip("/")
            login_cfg = raw.get("login", {})
            user_sel = login_cfg.get("user_selector", "input[type=text]")
            pass_sel = login_cfg.get("pass_selector", "input[type=password]")
            submit_sel = login_cfg.get("submit_selector", "button[type=submit]")
            page = ctx.new_page()
            for account in site.accounts:
                try:
                    logger.info(f"登录 WorkBuddy/{account.name}")
                    page.goto(base, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_selector(user_sel, timeout=8000)
                        page.fill(user_sel, account.user)
                        page.fill(pass_sel, account.password)
                        page.click(submit_sel)
                    except Exception:
                        logger.info("未检测到登录框，可能已登录")
                    if headful:
                        # 有界面模式下等待用户手动通过验证码/二次验证
                        logger.info("请在浏览器中完成验证码，等待 60 秒后自动保存...")
                        page.wait_for_timeout(60000)
                    else:
                        page.wait_for_timeout(8000)
                    cookies = ctx.cookies()
                    store.save(site.key, account.name, {"cookies": cookies, "expires_at": None})
                    logger.info(f"WorkBuddy/{account.name} cookies 已保存")
                except Exception as e:
                    logger.warning(f"WorkBuddy/{account.name} 抓取失败: {e}")
            browser.close()
    logger.info("完成。已将 cookies 写入 store/，可推送到仓库供 Actions 复用。")


if __name__ == "__main__":
    main()
