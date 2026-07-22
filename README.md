# 每日自动签到（acy7 + WorkBuddy）

通过 GitHub Actions **免费**定时运行：每天自动调用接口/驱动网页，完成
[acy7.com（New API 网关）](https://acy7.com) 与 **WorkBuddy** 的每日签到领积分，
结果推送到 Telegram / Server酱。

- acy7：纯 `requests` 直连 New API 接口（`/api/user/login` → `/api/user/checkin`）。
- WorkBuddy：用 Playwright 驱动其**网页端**自动登录并点击签到按钮（社区稳定方案）。
- 登录态通过 `actions/cache` 跨运行持久化，仅在失效时重新登录。
- 支持多账号（当前配置：acy7 ×1、WorkBuddy ×2）。

## 目录结构

```
common/        公共模块：config / session / store / logger / notify
signers/       各平台签到站：acy7.py、workbuddy.py
scripts/       capture_workbuddy_token.py（本地抓取 WorkBuddy 登录态）
.github/       Actions 工作流（每日定时 + 手动触发）
config.yaml    站点 / 账号(env引用) / 通知 / 选择器配置
main.py        编排入口
```

## 一、准备账号与通知渠道

1. **Telegram 通知（推荐）**
   - 找 [@BotFather](https://t.me/BotFather) 创建 Bot，拿到 `TG_TOKEN`（形如 `123456:AAxxx`）。
   - 给 Bot 发一条消息，再访问 `https://api.telegram.org/bot<TG_TOKEN>/getUpdates`
     拿到你的 `TG_CHAT_ID`（整数）。
   - 服务端酱可改用 `SERVERCHAN_KEY`（二选一，config.yaml 里 `channel` 切换）。

2. **账号**：准备 acy7 的账号密码、两个 WorkBuddy 账号密码。

## 二、部署到 GitHub（免费，零服务器）

1. 在 GitHub 新建一个 **Private** 仓库，把本目录内容推上去。
2. 仓库 `Settings → Secrets and variables → Actions → New repository secret` 添加：
   - `ACY7_USER` / `ACY7_PASS`
   - `WB1_USER` / `WB1_PASS`、`WB2_USER` / `WB2_PASS`
   - `TG_TOKEN` / `TG_CHAT_ID`（或 `SERVERCHAN_KEY`）
3. `Actions` 页面启用 workflow。可点 `Run workflow` 手动跑一次验证。
4. 之后每天 **北京时间 09:05** 自动执行（如需改时间改 `daily-checkin.yml` 的 cron）。

> 登录态缓存在 `actions/cache`（key `signin-token-cache`），token 失效会自动重登。

## 三、本地调试 / 运行

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium
cp .env.example .env      # 填入真实账号与 TG_*
python main.py
```

本地运行同样会把登录态写到 `store/tokens.json`（已被 .gitignore 忽略，不会泄露）。

## 四、WorkBuddy 登录需要验证码时

若 WorkBuddy 网页登录触发验证码/设备绑定，CI 无头浏览器无法自动通过：
本地以有界面模式运行抓取脚本，手动过验证码，自动把 cookies 写入 `store/`：

```bash
CAPTURE_HEADFUL=1 python scripts/capture_workbuddy_token.py
```

提交/缓存后，Actions 上的日常签到直接复用这些 cookies，无需再次登录。

> 若你的 WorkBuddy 签到入口是**桌面客户端**而非网页，请把 `config.yaml` 里
> `workbuddy.base_url` 改为可网页访问的地址；或仅在本地用 Windows 计划任务运行该账号。

## 五、配置调优

- `config.yaml → sites.workbuddy.login/checkin`：按实际页面调整选择器/文案。
- 多账号：在对应 `accounts` 列表继续追加，并补 `WB3_*` 等 Secrets 与 workflow `env`。
- 通知：`notify.channel` 可选 `telegram` / `serverchan` / `none`。

## 合规提示

仅用于本人账号的每日签到个人自动化；请遵守两平台服务条款，勿用于刷量或滥用多账号。
