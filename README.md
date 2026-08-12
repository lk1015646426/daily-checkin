# 云签到

一个基于 Python 和 GitHub Actions 的每日自动签到脚本，目前支持：

- **acy7**：使用账号密码登录并签到
- **WorkBuddy / CodeBuddy**：使用桌面客户端提取的 access token 签到
- **TRAE**：使用桌面客户端提取的 refresh token 签到
- **通知**：支持 Telegram、Server酱，或关闭通知

脚本可以通过 GitHub Actions 定时运行，也可以在本地手动执行。

> 本项目仅用于本人账号的个人自动化。请遵守相关平台的服务条款，不要用于刷量、滥用多账号或其他违规用途。

## 目录结构

```text
common/                       公共模块：配置、会话、缓存、日志和通知
signers/                      各平台签到实现
.github/workflows/            GitHub Actions 工作流
config.yaml                   站点、账号别名、通知和重试配置
.env.example                  本地环境变量示例
main.py                       程序入口
requirements.txt              Python 依赖
logs/.gitkeep                 日志目录占位文件
```

以下内容属于本地或运行时数据，已通过 `.gitignore` 排除：

- `.env`：本地真实凭证
- `store/`：登录态和 token 缓存
- `logs/*.log`：运行日志
- `.workbuddy/`：本地工作记忆
- `__pycache__/`、`*.pyc`：Python 缓存

## GitHub Actions 部署

### 1. 准备仓库

建议使用 **Private 仓库** 保存本项目。将代码推送到 GitHub 后，在仓库中打开：

`Settings → Secrets and variables → Actions → New repository secret`

### 2. 添加 Secrets

根据实际启用的站点添加以下 Secrets。

#### acy7

| Secret | 说明 |
| --- | --- |
| `ACY7_USER` | acy7 用户名 |
| `ACY7_PASS` | acy7 密码 |

#### WorkBuddy / CodeBuddy

| Secret | 说明 |
| --- | --- |
| `WB1_TOKEN` | 第一个 WorkBuddy 账号的 access token |
| `WB2_TOKEN` | 第二个 WorkBuddy 账号的 access token |

#### TRAE

| Secret | 说明 |
| --- | --- |
| `TRAE1_TOKEN` | TRAE 账号 `1780293` 的 refresh token |
| `TRAE1_DEVICE_ID` | TRAE 账号 `1780293` 的客户端设备 ID |
| `TRAE2_TOKEN` | TRAE 账号 `1920293` 的 refresh token |
| `TRAE2_DEVICE_ID` | TRAE 账号 `1920293` 的客户端设备 ID |

两个账号的 Token 和设备 ID 必须分别从各自的登录状态提取并成对填写，不能把一个账号的 Token 与另一个账号的设备 ID 混用。

#### 通知（二选一）

Telegram：

| Secret | 说明 |
| --- | --- |
| `TG_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | Telegram Chat ID |

Server酱：

| Secret | 说明 |
| --- | --- |
| `SERVERCHAN_KEY` | Server酱 SendKey |

不使用通知时，将 `config.yaml` 中的通知渠道改为：

```yaml
notify:
  channel: none
```

### 3. 执行时间

工作流文件位于 `.github/workflows/daily-checkin.yml`，默认配置为：

- **每天北京时间 04:00 执行**
- 对应 UTC 时间为前一天 20:00
- cron 表达式为 `0 20 * * *`
- 支持在 GitHub Actions 页面点击 **Run workflow** 手动运行

GitHub Actions 的 cron 使用 UTC，并且定时任务可能因平台负载稍有延迟。

### 4. 查看运行结果

在仓库的 **Actions** 页面查看运行日志。只要任意账号签到失败，程序就会以退出码 `1` 结束，使该次工作流显示失败；通知仍会尝试发送完整汇总。

## 本地运行

建议使用 Python 3.11。Windows 示例：

```powershell
cd 'C:\路径\云签到'

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
# 编辑 .env，填入本地测试所需的账号、token 和通知配置

python main.py
```

Linux/macOS 示例：

```bash
cd /path/to/云签到
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python main.py
```

不要把真实凭证写入 `config.yaml`，也不要提交 `.env`、token 缓存或运行日志。

## 配置说明

`config.yaml` 保存站点结构和环境变量名称，不保存账号密码或 token：

- `sites.<site>.enabled`：是否启用站点
- `sites.<site>.accounts`：账号别名和对应的环境变量名
- `notify.channel`：`telegram`、`serverchan` 或 `none`
- `retry.attempts`：失败重试次数
- `retry.backoff_seconds`：重试间隔秒数

当前账号使用脱敏别名，不在现行配置中保存完整手机号。新增账号时，请使用别名，并在 `.env.example`、GitHub Actions 工作流和 GitHub Secrets 中同步增加对应变量。

## Token 获取说明

### WorkBuddy / CodeBuddy

在已经登录的桌面客户端中找到认证文件，读取其中的 `auth.accessToken`，然后保存到对应的 GitHub Secret：

```text
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
```

不同客户端版本的文件位置可能不同。不要把认证文件或 token 提交到 Git。

### TRAE

在已经登录的 TRAE 客户端中找到 `storage.json`，提取 refresh token 和设备 ID：

```text
%APPDATA%\TRAE SOLO CN\User\globalStorage\storage.json
```

- 账号 `1780293` 的 refresh token 和 `telemetry.devDeviceId` 分别填入 `TRAE1_TOKEN`、`TRAE1_DEVICE_ID`
- 账号 `1920293` 的 refresh token 和 `telemetry.devDeviceId` 分别填入 `TRAE2_TOKEN`、`TRAE2_DEVICE_ID`
- Token 与设备 ID 必须来自同一个账号的登录状态，不得交叉使用
- refresh token 失效后，需要重新登录对应账号并更新该账号的两个 GitHub Secrets
- 如果账号 `1780293` 返回 401，请优先重新提取并更新 `TRAE1_TOKEN` 和 `TRAE1_DEVICE_ID`；工作流不会在日志或通知中输出真实凭证

TRAE 签到完成后会额外查询账户权益余额，因此通知会与 WorkBuddy 使用相同的信息结构，例如：

```text
✅ TRAE 1780293：+200积分 | 余额 1600积分
```

## 安全注意事项

- 不要提交 `.env`、`store/`、token 文件、客户端认证文件或运行日志。
- 不要在 Issue、Pull Request 或日志中粘贴密码、token、cookie、Bot Token 或 SendKey。
- 如果凭证曾经泄露，应立即在对应平台撤销或更换，并同步更新 GitHub Secrets。
- 即使仓库设置为 Private，也应继续按照敏感凭证标准管理代码和提交记录。

## 许可证

当前仓库未声明开源许可证。如果以后公开分发，请先补充合适的 LICENSE 文件；仅在 Private 仓库中自用时可暂不添加。
