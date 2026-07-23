"""本地一次性工具：手动登录 WorkBuddy，导出 cookies 供存入 GitHub Secrets。

WorkBuddy 用手机号+验证码 / 微信扫码登录，CI 无法自动登录，故需你本机手动登一次：
  1) pip install -r requirements.txt && python -m playwright install chromium
  2) python scripts/capture_workbuddy_token.py
  3) 按提示在弹出的浏览器中为 each 账号完成登录（收验证码 / 扫微信码）
  4) 登录成功后回到终端按回车，脚本打印该账号的 cookies JSON
  5) 将 JSON 整段复制进对应 GitHub Secrets（WB1_COOKIES / WB2_COOKIES）

cookies 失效后（一般数天~数周）重复上述步骤更新 Secrets 即可。
"""
import json
import os
import sys

# 把项目根目录加入模块搜索路径（脚本在 scripts/ 下，否则找不到 common 包）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import Config
from common.logger import setup_logger


def main():
    logger = setup_logger("capture")
    cfg = Config.load("config.yaml", logger)
    # require_credentials=False：抓取场景下账号本就还没有 cookies，需先手动登录
    wb_sites = [s for s in cfg.sites(require_credentials=False) if s.type == "workbuddy"]
    if not wb_sites:
        logger.error("config.yaml 未启用 workbuddy 站点")
        sys.exit(1)

    # 可选：只抓指定账号（如 `python scripts/capture_workbuddy_token.py acc1`），
    # 避免覆盖已成功抓取的其他账号
    only = sys.argv[1] if len(sys.argv) > 1 else None

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        for site in wb_sites:
            base = site.base_url.rstrip("/")
            for account in site.accounts:
                if only and account.name != only:
                    continue
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
                    # 直接保存到文件，避免手动复制超长 JSON 出错（store/ 已 gitignore）
                    store_dir = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "store",
                    )
                    os.makedirs(store_dir, exist_ok=True)
                    cookie_file = os.path.join(store_dir, f"wb_cookies_{account.name}.json")
                    with open(cookie_file, "w", encoding="utf-8") as f:
                        json.dump(cookies, f, ensure_ascii=False)
                    print(
                        f"\n===== 账号 [{account.name}] 的 cookies "
                        f"（请整段复制到 GitHub Secrets: {secret_name}）====="
                    )
                    print(json.dumps(cookies, ensure_ascii=False))
                    print("=" * 60)
                    print(f"[已自动保存] {cookie_file}")
                    print()
                except Exception as e:
                    logger.warning(f"账号 {account.name} 抓取失败: {e}")
                finally:
                    ctx.close()
        browser.close()
    logger.info("完成。请将上面的 cookies JSON 分别填入对应 Secrets。")


if __name__ == "__main__":
    main()
