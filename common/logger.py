"""日志初始化：控制台 + 滚动文件，提供敏感信息脱敏工具。"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name="checkin", log_dir="logs", level=logging.INFO):
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:
        return logger
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    fh = RotatingFileHandler(
        os.path.join(log_dir, "checkin.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


def redact(text, secrets=None):
    """将敏感串替换为 ***，避免日志泄露密码/Token。"""
    s = str(text)
    for sec in secrets or []:
        if sec:
            s = s.replace(sec, "***")
    return s
