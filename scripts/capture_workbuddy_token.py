"""本地一次性工具：手动登录 WorkBuddy，导出 cookies 供存入 GitHub Secrets。

WorkBuddy 用手机号+验证码 / 微信扫码登录，CI 无法自动登录，故需你本机手动登一次：
  1) pip install -r requirements.txt && playwright install chromium
  2) python scripts/capture_workbuddy_token.py
  3) 按提示在弹出的浏览器中为 each 账号完成登录（收验证码 / 扫微信码）
  4) 登录成功后回到终端按回车，脚本打印该账号的 cookies JSON
  5) 将 JSON 整段复制进对应 GitHub Secrets（WB1_COOKIES / WB2_COOKIES）

cookies 失效后（一般数天~数周）重复上述步骤更新 Secrets 即可。
"""
import json
import sys

from common.config import Config
from common.logger import setup_logger


def main():
    logger = setup_logger("capture")
    cfg = Config.load("config.yaml", logger)
    wb_sites = [s for s in cfg.sites() if s.type == "workbuddy"]
    if not wb_sites:
        logger.error("config.yaml 未启用 workbuddy 站点")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headful=True)
        for site in wb_sites:
            base = site.base_url.rstrip("/")
            for account in site.accounts:
                ctx = browser.new_context()
                page = ctx.new_page()
                try:
                    logger.info(f"打开 WorkBuddy 登录页: {base}")
                    page.goto(base, wait_until="domcontentloaded", timeout=60000)
                    input(
                        f"\n[账号 {account.name}] 请在浏览器中完成登录"
                        f"（手机验证码 / 微信扫码），登录成功后回到此处按回车继续..."
                    )
                    cookies = ctx.cookies()
                    secret_name = account.cookies_env or f"{account.name.upper()}_COOKIES"
                    print(
                        f"\n===== 账号 [{account.name}] 的 cookies "
                        f"（请整段复制到 GitHub Secrets: {secret_name}）====="
                    )
                    print(json.dumps(cookies, ensure_ascii=False))
                    print("=" * 60 + "\n")
                except Exception as e:
                    logger.warning(f"账号 {account.name} 抓取失败: {e}")
                finally:
                    ctx.close()
        browser.close()
    logger.info("完成。请将上面的 cookies JSON 分别填入对应 Secrets。")


if __name__ == "__main__":
    main()
