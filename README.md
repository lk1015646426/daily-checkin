# 每日自动签到（acy7 + WorkBuddy）

通过 GitHub Actions **免费**定时运行：每天自动调用接口 / 驱动网页，完成
[acy7.com（New API 网关）](https://acy7.com) 与 **WorkBuddy** 的每日签到领积分，
结果推送到 Telegram / Server酱。

- acy7：纯 `requests` 直连 New API 接口（`/api/user/login` → `/api/user/checkin`），账号密码自动登录。
- WorkBuddy：用 Playwright 驱动其**网页端**；但登录方式是手机号+验证码 / 微信扫码，CI 无法自动登录，
  故需你**本机手动登录一次**，导出登录态 cookies 存入 GitHub Secrets，之后 CI 每天带 cookies 自动签到。
- 支持多账号（当前配置：acy7 ×1、WorkBuddy ×2）。

## 目录结构

```
common/        公共模块：config / session / store / logger / notify
signers/       各平台签到站：acy7.py、workbuddy.py
scripts/       capture_workbuddy_token.py（本机抓取 WorkBuddy 登录态 cookies）
.github/       Actions 工作流（每日定时 + 手动触发）
config.yaml    站点 / 账号(env引用) / 通知 / 选择器配置
main.py        编排入口
```

## 一、准备

### 1. 通知渠道
- **Telegram（推荐）**：找 [@BotFather](https://t.me/BotFather) 创建 Bot，拿 `TG_TOKEN`（形如 `123456:AAxxx`）；
  给 Bot 发一条消息后访问 `https://api.telegram.org/bot<TG_TOKEN>/getUpdates` 拿 `TG_CHAT_ID`。
- **Server酱（微信，更省事）**：去 serverchan.com 微信登录拿 `SERVERCHAN_KEY`，
  并在 `config.yaml` 把 `notify.channel` 改成 `serverchan`。
- 二选一，`config.yaml` 的 `channel` 控制。

### 2. 账号与登录态
- **acy7**：准备账号密码，直接填 Secrets `ACY7_USER` / `ACY7_PASS`。
- **WorkBuddy（两个账号）**：**不能用账号密码自动登录**（手机号+验证码/微信扫码）。
  需本机运行 `scripts/capture_workbuddy_token.py` 手动登录，把导出的 cookies JSON
  分别填进 Secrets `WB1_COOKIES` / `WB2_COOKIES`（详见第四节）。

## 二、部署到 GitHub（免费，零服务器）

1. 新建 **Private** 仓库并推送本目录（已完成）。
2. 仓库 `Settings → Secrets and variables → Actions → New repository secret` 添加：
   - `ACY7_USER` / `ACY7_PASS`
   - `WB1_COOKIES` / `WB2_COOKIES`（cookies JSON，来自 capture 脚本）
   - `TG_TOKEN` / `TG_CHAT_ID`（或 `SERVERCHAN_KEY`）
3. `Actions` 页面启用 workflow，点 `Run workflow` 手动验证。
4. 之后每天 **北京时间 09:05** 自动执行（改时间改 `daily-checkin.yml` 的 cron）。

> acy7 登录态走 `actions/cache` 自动复用/重登；WorkBuddy 登录态即 Secrets 中的 cookies。

## 三、本地运行 / 调试

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium
cp .env.example .env      # acy7 填账号密码；WB 填 cookies JSON
python main.py
```

本地运行同样会把 acy7 登录态写到 `store/tokens.json`（已被 .gitignore 忽略，不泄露）。

## 四、获取 WorkBuddy 登录态（必做）

WorkBuddy 需手机验证码 / 微信扫码，CI 无法自动，因此首次（及 cookies 失效时）需本机做一次：

```bash
pip install -r requirements.txt
playwright install chromium
python scripts/capture_workbuddy_token.py
```

脚本会弹出浏览器，为 `acc1`、`acc2` 依次手动登录；登录成功后终端打印该账号的 cookies JSON。
将每个账号的 JSON **整段**复制进对应 Secrets（`WB1_COOKIES` / `WB2_COOKIES`）。
cookies 一般数天~数周有效，失效后重复本步骤更新 Secrets 即可。

## 五、配置调优

- `config.yaml → sites.workbuddy.checkin`：按实际页面调整选择器/文案。
- 多账号：在 `accounts` 继续追加（如 `name: acc3, cookies_env: WB3_COOKIES`），并补 Secrets 与 workflow `env`。
- 通知：`notify.channel` 可选 `telegram` / `serverchan` / `none`。

## 合规提示

仅用于本人账号的每日签到个人自动化；请遵守两平台服务条款，勿用于刷量或滥用多账号。
