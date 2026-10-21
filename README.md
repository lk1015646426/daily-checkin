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
| `WORKBUDDY_ACCOUNTS_JSON` | 推荐。桌面工具自动维护的多账号聚合 Secret，格式为带 `version: 1` 的账号数组。 |
| `WB1_TOKEN` | 第一个 WorkBuddy 账号的 access token |
| `WB2_TOKEN` | 第二个 WorkBuddy 账号的 access token |

`WORKBUDDY_ACCOUNTS_JSON` 存在且格式合法时优先使用，支持任意数量账号；合法空账号数组会明确停用所有 WorkBuddy 签到。未部署该 Secret 或格式无效时，工作流继续兼容 `WB1_TOKEN`、`WB2_TOKEN`。

聚合 Secret 只包含稳定账号键、用于通知的显示名和 access token，不包含完整认证文件、refresh token、session state 或手机号。建议由桌面端的 WorkBuddy 页面同步，不要手工粘贴完整认证文件。

#### TRAE

| Secret | 说明 |
| --- | --- |
| `A_1780293_TOKEN` | TRAE 账号 `1780293` 的 access token |
| `A_1780293_DEVICE_ID` | TRAE 账号 `1780293` 的数字设备 ID（icube-dc） |
| `A_1780293_DEVICE_BRAND` / `A_1780293_DEVICE_TYPE` | 设备品牌与类型 |
| `A_1780293_REFRESH_JSON` | 云端自主续期材料（refresh token + 设备密钥对，由种子脚本写入） |
| `A_1920293_*` | 账号 `1920293` 的同名五项 |

两个账号的 Token 和设备 ID 必须分别从各自的登录状态提取并成对填写，不能把一个账号的 Token 与另一个账号的设备 ID 混用。配置了 `*_REFRESH_JSON` 后，云端会在 access token 剩余有效期不足 6 小时时自动调用 ExchangeToken 换新（refresh token 每次轮换，最新值随 actions/cache 持久化），无需每天手动更新 Token。

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

### WorkBuddy 积分活动（第一版）

桌面「切换应用 → WorkBuddy → 积分活动与成长中心」可开启并同步：

- 活动补偿：先查是否可领，再领取并回读确认；已经领过或活动未开放时正常跳过。
- 成长任务：接取当前开放的任务，补领服务端已判定完成的奖励。接取不代表完成，不自动生成对话、技能或小程序使用记录。
- Buddy 旅行：领取返回的礼物，确认领取成功后按当天剩余名额派出。没有 Buddy 或状态未知时报告原因。
- 到期提醒：查询任务截止时间与可用积分包到期时间，默认提前 7 天提醒。

仅已开启自动签到的账号参与。首次使用需在桌面开启活动总开关，点击「保存并同步云端」。未提供活动配置时保持关闭，旧版多账号 Secret 仍可使用。配置以 `WORKBUDDY_ACCOUNTS_JSON` 的可选顶层 `activities` 字段保存，不添加 refresh token 或完整认证文件。

积分余额采用官方 `get-user-resource-summary` 接口的 `Packages[].CycleRemainCapacity` 合计，包含官方计入的基础、购买和活动积分，最多显示两位小数；不再按旧资源包类型排除体验版的 500 积分。活动执行后重新查询余额，不把领奖金额自行加到旧余额上。无法查询时显示「余额未查询到」，不使用 0 冒充查询结果。桌面 WorkBuddy 页面打开时每分钟更新，并显示查询时间。

```json
{"enabled":true,"compensation":true,"tasks":true,"travel":true,"expiryDays":7}
```

每天北京时间 04:00 随全部站点签到执行；00:00、08:00、12:00、16:00、20:00 仅检查 WorkBuddy 活动。可手动运行工作流并勾选 `activities_only`，还可沿用 `account_filter` 指定一个账号。定时任务可能因 GitHub 调度延迟。

写操作不自动重试，成功必须有服务端状态回读。未确认的操作会报告异常，不继续执行依赖它的下一步。每个账号有时间预算，各账号分别汇报；活动报告保存在 `store/workbuddy_activities.json`，随原有缓存保存，不包含凭证。

通知采用**每天一次完整汇总**：04:00 计划任务随每日签到汇总所有账号，其余活动轮询全部静默，包括新领奖、到期提醒和异常。轮询期间接取、领奖和旅行的累计结果保存在 `store/daily_summary.json`，并入下一次完整汇总。单账号手动验证不发送通知；同一天重复运行完整任务也不重复发送。只有平台确认接收才记为当天已发；发送失败保留累计结果并将任务标为失败。GitHub 调度可能延迟，实际发送时间以运行记录为准。不要删除云端 `store` 缓存，否则会丢失每日去重和待汇总记录。

旅行接口中的 `buddy_id=0` 可能只是当前没有旅行绑定；脚本会再查询 `/buddy/info` 确认是否拥有 Buddy，不再直接判为未领取。

凭证继续由桌面端统一续期、同步至云端。抽奖、能量盲盒、补登卡消耗、模拟使用行为、短信登录和公众号关注不在这一版范围内。

接口契约参考：
[88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)、
[ithtelab/workbuddy-manager](https://github.com/ithtelab/workbuddy-manager)、
[L0NE-6/WorkBuddy-Daily](https://github.com/L0NE-6/WorkBuddy-Daily)。
本项目活动模块独立实现，是否开放、奖励数额及完成条件以账号实际接口返回为准。

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

桌面端支持在 WorkBuddy 页面设置自定义 EXE 与认证文件路径。切换时会先请求官方客户端正常退出；只有用户在确认框中明确同意后才会强制结束进程。取消确认不会修改认证文件。

### TRAE

Token 与设备 ID 由切换工具从官方客户端快照同步；云端自主续期材料由种子脚本写入：

- 运行 `切换应用\daily-checkin-additions\seed_trae_refresh_secrets.py`（dry-run 检查完整性，`--apply` 实际写入）
- 脚本从切换工具的加密账号库提取 refresh token、设备密钥对、数字设备 ID 和 machine ID，打包为 `A_<账号>_REFRESH_JSON` Secret
- 账号 `1780293`、`1920293` 各对应一组 `A_<账号>_*` Secrets，不得交叉使用
- refresh token 有效期约 6 个月且每次刷新轮换；轮换链断裂（如缓存丢失且 Secret 过期）时，需在 TRAE 客户端重新登录该账号后重跑种子脚本
- 如果某账号签到返回 401，请优先重跑种子脚本更新该账号的 Secrets；工作流不会在日志或通知中输出真实凭证

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
