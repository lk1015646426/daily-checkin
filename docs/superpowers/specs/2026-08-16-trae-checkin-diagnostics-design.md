# TRAE 签到诊断增强设计

日期：2026-08-16

## 背景

TRAE 签到目前在 GitHub Actions 和本地控制面板中都可能返回“操作太过频繁啦，请稍后尝试”。已经在官方客户端领取过的账号，会因为 `status.checked_in == true` 被直接显示为成功，并不会执行 `claim`。因此现有结果不足以判断失败来自凭证、云端网络、批量频率，还是直接 API 请求缺少官方客户端上下文。

本次改动只增强诊断能力，不尝试猜测或补充 Cookie、设备签名等未知认证字段，也不自动重试 `claim`。

## 目标

1. 云端签到可以只运行一个指定的 TRAE 账号，形成受控实验。
2. 云端和本地结果明确区分 `status` 与 `claim` 阶段。
3. 日志包含 HTTP 状态、业务码、是否进入 `claim`、token 有效期状态和设备 ID 是否存在。
4. 界面明确区分“查询到今日已签到”和“本次领取成功”。
5. GitHub Actions 避免定时任务与手动任务重叠运行。
6. 所有诊断输出均不得泄露 token、Cookie、Authorization 或完整设备 ID。

## 非目标

- 不向签到请求添加 Cookie、签名、machine ID 或未经验证的请求头。
- 不修改 TRAE `status`、`claim` 接口地址和领取业务流程。
- 不对“操作太过频繁”立即重试。
- 不承诺本次改动直接修复签到，只保证能获得足够证据定位下一步。

## 云端 Python 设计

### 单账号选择

`main.py` 支持可选环境变量 `CHECKIN_ACCOUNT_FILTER`。值采用 `站点:账号名` 格式，例如：

```text
trae:刘浩17721
```

未设置时保持现有全量签到行为。设置后只运行完全匹配的账号；找不到匹配项时以失败退出并输出不含凭证的错误。

GitHub Actions 的 `workflow_dispatch` 增加可选输入 `account_filter`，并传入 `CHECKIN_ACCOUNT_FILTER`。定时任务不传输入，仍执行全部账号。

### 诊断结果

TRAE 请求辅助函数返回 HTTP 状态和 JSON 数据。签到结果增加以下内部字段：

- `stage`: `status`、`claim` 或 `usage`
- `business_code`: 服务端业务码，不存在时为 `null`
- `claim_attempted`: 是否执行过领取
- `token_expiry_state`: `valid`、`expired` 或 `unknown`
- `device_present`: 是否存在设备 ID

日志输出这些字段，但通知正文继续保持简洁。凭证值和完整设备标识不得进入日志。

### 并发保护

workflow 增加仓库级 `concurrency`：同一签到 workflow 同时只允许一个任务运行，后启动的任务不取消正在运行的任务，避免重复领取。

## 本地控制面板设计

本地签到结果结构增加：

- `outcome`: `already_checked_in`、`claimed` 或 `failed`
- `stage`
- `business_code`
- `http_status`
- `claim_attempted`
- `token_expiry_state`
- `device_present`

界面文案按结果显示：

- `already_checked_in`: “查询到今日已签到，本次未发起领取”
- `claimed`: “本次领取成功”
- `failed`: “在 claim/status 阶段失败”并显示业务码

本地请求仍保持当前请求形状，不带 Cookie，确保本次改动只增加观测能力。

## 安全要求

诊断信息只允许输出布尔值、状态枚举、HTTP 状态和业务码。以下数据不得写入日志、UI 错误详情或测试快照：

- access token
- refresh token
- Authorization
- Cookie
- 完整 device ID
- GitHub PAT 或 Secret 内容

账号显示继续使用现有别名。

## 测试设计

云端 Python 测试覆盖：

1. 未设置筛选时运行全部账号。
2. 设置 `trae:<账号>` 时只运行目标账号。
3. 无匹配账号时明确失败。
4. `status` 已签到时 `claim_attempted == false`。
5. `claim` 业务失败时保留阶段和业务码，且不重试。
6. 日志诊断文本不包含 token 和完整设备 ID。
7. workflow 包含单账号输入与并发保护。

本地 Rust/TypeScript 测试覆盖：

1. 已签到映射为 `already_checked_in`，不调用 `claim`。
2. 本次领取成功映射为 `claimed`。
3. `claim` 失败保留阶段、HTTP 状态和业务码。
4. UI 分别显示“查询到今日已签到”和“本次领取成功”。
5. 诊断结果不包含任何原始凭证字段。

## 验证流程

改动完成后按以下顺序验证：

1. 运行 Python TRAE 单元测试。
2. 运行 Rust 相关测试。
3. 运行前端类型检查和相关测试。
4. 使用 GitHub Actions 手动输入一个未签到账号进行单账号测试。
5. 在同一账号官方客户端已登录但尚未领取时执行一次本地签到。
6. 根据 `stage`、业务码和本地/云端差异决定下一阶段是否需要研究 Cookie 或设备签名。

## 成功标准

一次测试后能够无歧义回答以下问题：

- `status` 是否成功？
- 是否真正调用了 `claim`？
- `claim` 的 HTTP 状态和业务码是什么？
- 本地与云端在同一账号上的结果是否不同？
- 显示成功是“之前已签到”还是“本次领取成功”？

