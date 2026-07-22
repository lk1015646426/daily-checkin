"""HTTP 会话封装：统一 UA/超时/重试，以及 Cloudflare 拦截识别。"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def create_session(timeout=30):
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.request_timeout = timeout
    return s


def detect_cloudflare(status_code, text):
    """粗略判断是否为 Cloudflare 人机验证拦截页。"""
    if status_code in (403, 503):
        low = (text or "").lower()
        if any(
            k in low
            for k in ("cloudflare", "just a moment", "verify you are human", "cf-mitigated")
        ):
            return True
    return False
