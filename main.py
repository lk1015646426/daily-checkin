"""每日签到编排入口：加载配置 -> 遍历各站点/账号 -> 聚合结果 -> 推送通知。

退出码：任一账号失败则退出 1（便于 GitHub Actions 标记失败），但通知已发出。
"""
import os
import sys

from common.config import Config
from common.logger import setup_logger
from common.notify import Notifier
from common.session import create_session
from common.store import TokenStore
from signers.acy7 import Acy7Signer
from signers.workbuddy import WorkBuddySigner

SIGNERS = {
    "acy7": Acy7Signer,
    "workbuddy": WorkBuddySigner,
}


def main():
    logger = setup_logger()
    cfg = Config.load("config.yaml", logger)
    store = TokenStore("store/tokens.json")
    session = create_session()
    notifier = Notifier(cfg.get_notify(), logger)

    results = []
    for site in cfg.sites():
        signer_cls = SIGNERS.get(site.type)
        if not signer_cls:
            logger.warning(f"未知站点类型: {site.type}，已跳过")
            continue
        for account in site.accounts:
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
                f"{res['site']}/{res['account']}: {'成功' if res['ok'] else '失败'} - {res['msg']}"
            )
            results.append(res)

    summary = Notifier.format_summary(results)
    logger.info("\n" + summary)
    notifier.send(summary)

    failed = [r for r in results if not r["ok"]]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
