"""每日签到编排入口：加载配置 -> 遍历各站点/账号 -> 聚合结果 -> 推送通知。

退出码：任一账号失败则退出 1（便于 GitHub Actions 标记失败），但通知已发出。
"""
import os
import sys

from dotenv import load_dotenv

from common.config import Account, Config

load_dotenv()  # 读取项目根目录 .env（账号、token 与通知凭证）
from common.logger import setup_logger
from common.notify import Notifier
from common.session import create_session
from common.store import TokenStore
from signers.acy7 import Acy7Signer
from signers.workbuddy import WorkBuddySigner
from signers.trae import TraeSigner
from signers.zhipu import ZhipuSigner

SIGNERS = {
    "acy7": Acy7Signer,
    "workbuddy": WorkBuddySigner,
    "trae": TraeSigner,
    "zhipu": ZhipuSigner,
}


def parse_account_filter(value):
    """解析可选的 `站点:账号名` 筛选条件。"""
    if value is None or not value.strip():
        return None
    site, separator, account = value.partition(":")
    site = site.strip()
    account = account.strip()
    if not separator or not site or not account:
        raise ValueError("账号筛选格式应为 站点:账号名")
    return site, account


def account_matches_filter(site_name, account: Account, selector):
    """按稳定键筛选动态 WorkBuddy/智谱账号，其他站点保持名称匹配。"""
    if site_name in ("workbuddy", "zhipu") and account.stable_key:
        return account.stable_key == selector
    return account.name == selector


def _diagnostic_suffix(result):
    fields = []
    for key in (
        "stage",
        "business_code",
        "http_status",
        "claim_attempted",
        "token_expiry_state",
        "device_present",
    ):
        if key in result:
            fields.append(f"{key}={result.get(key)}")
    return f" [{', '.join(fields)}]" if fields else ""


def main():
    logger = setup_logger()
    cfg = Config.load("config.yaml", logger)
    store = TokenStore("store/tokens.json")
    session = create_session()
    notifier = Notifier(cfg.get_notify(), logger)

    try:
        account_filter = parse_account_filter(os.environ.get("CHECKIN_ACCOUNT_FILTER"))
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    results = []
    matched_accounts = 0
    for site in cfg.sites():
        signer_cls = SIGNERS.get(site.type)
        if not signer_cls:
            logger.warning(f"未知站点类型: {site.type}，已跳过")
            continue
        for account in site.accounts:
            if account_filter and account_filter[0] != site.key:
                continue
            if account_filter and not account_matches_filter(site.key, account, account_filter[1]):
                continue
            matched_accounts += 1
            try:
                signer = signer_cls(site, account, session, store, logger, notifier)
                res = signer.run()
            except Exception as e:
                logger.exception(f"{site.key}/{account.name} 执行异常")
                res = {
                    "site": site.key,
                    "account": account.name,
                    "ok": False,
                    "points": None,
                    "msg": f"异常: {e}",
                    "cached": False,
                }
            logger.info(
                f"{res['site']}/{res['account']}: {'成功' if res['ok'] else '失败'} - "
                f"{res['msg']}{_diagnostic_suffix(res)}"
            )
            results.append(res)

    if account_filter and matched_accounts == 0:
        site_name, account_name = account_filter
        logger.error(f"未找到筛选账号: {site_name}/{account_name}")
        sys.exit(1)

    summary = Notifier.format_summary(results)
    logger.info("\n" + summary)
    notifier.send(summary)

    failed = [r for r in results if not r["ok"]]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
