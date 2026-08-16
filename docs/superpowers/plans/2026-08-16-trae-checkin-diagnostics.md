# TRAE Checkin Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为云端签到和本地控制面板补齐单账号实验、`status/claim` 阶段诊断、脱敏结果展示和 workflow 并发保护，定位 TRAE 领取失败原因而不改变未知认证协议。

**Architecture:** 云端 Python 在编排层筛选账号，在 TRAE signer 的结果对象中携带阶段和业务诊断字段；本地 Rust 在签到结果模型中携带同样的阶段化字段，前端只展示安全的枚举和状态。GitHub Actions 通过 workflow 输入传递筛选条件，并用 concurrency 防止重复运行。

**Tech Stack:** Python 3.11、unittest、PyYAML、requests；Rust/Tauri、serde、reqwest；React/TypeScript；GitHub Actions YAML。

## Global Constraints

- 本次只增强诊断能力，不向签到请求添加未经验证的 Cookie、签名、machine ID 或其他认证字段。
- 不对“操作太过频繁”自动重试，不修改 `status`、`claim` 接口地址和领取顺序。
- 日志和 UI 不得输出 token、Cookie、Authorization、完整 device ID、GitHub PAT 或 Secret 内容。
- 未设置筛选条件时必须保持现有全量签到行为。
- 已签到状态必须明确标记为“本次未发起领取”。

---

### Task 1: 云端单账号筛选与诊断结果

**Files:**
- Modify: `云签到/main.py`
- Modify: `云签到/signers/base.py`
- Modify: `云签到/signers/trae.py`
- Test: `云签到/tests/test_trae.py`

**Interfaces:**
- `main.py` 提供 `parse_account_filter(value: str | None) -> tuple[str, str] | None`，接受 `site:account`，空值返回 `None`，非法格式抛出 `ValueError`。
- `TraeSigner.checkin()` 的结果保留现有 `ok/points/msg` 字段，并增加 `stage`, `business_code`, `http_status`, `claim_attempted`, `token_expiry_state`, `device_present`。
- `BaseSigner._result()` 透传这些可选诊断字段，不改变通知格式。

- [ ] **Step 1: Write failing tests for filter parsing and result propagation**

在 `tests/test_trae.py` 添加以下行为测试：

```python
def test_account_filter_parses_site_and_name(self):
    self.assertEqual(parse_account_filter("trae:刘浩17721"), ("trae", "刘浩17721"))

def test_account_filter_rejects_missing_separator(self):
    with self.assertRaises(ValueError):
        parse_account_filter("trae")

def test_claim_business_failure_keeps_stage_code_and_does_not_retry(self):
    result = signer.checkin({"token": "t", "device_id": "d"})
    self.assertEqual(result["stage"], "claim")
    self.assertEqual(result["business_code"], 500)
    self.assertTrue(result["claim_attempted"])
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing behavior**

Run:

```powershell
cd 'C:\Users\10156\Desktop\脚本\云签到'
python -m unittest tests.test_trae
```

Expected: failure mentioning `parse_account_filter` or missing diagnostic keys, not an import/environment error.

- [ ] **Step 3: Implement the minimal cloud behavior**

在 `main.py` 增加筛选解析和遍历过滤；无筛选时保持原循环。筛选没有匹配项时追加一个安全的失败结果并保持退出码 1。

在 `trae.py` 中增加两个明确的无副作用辅助方法：`_token_expiry_state(self, token: str | None = None) -> str` 和 `_current_device_id(self) -> str | None`，分别返回 `valid/expired/unknown` 和当前账号设备 ID。随后增加：

```python
def _diagnostic(self, stage, data=None, response=None, claim_attempted=False):
    return {
        "stage": stage,
        "business_code": (data or {}).get("code"),
        "http_status": response.status_code if response is not None else None,
        "claim_attempted": claim_attempted,
        "token_expiry_state": self._token_expiry_state(),
        "device_present": bool(self._current_device_id()),
    }
```

将 `status` 业务失败标记为 `stage=status`，将 `claim` 业务失败标记为 `stage=claim`，将余额查询异常标记为 `stage=usage`；不记录实际凭证。`BaseSigner._result()` 仅复制这些字段。

- [ ] **Step 4: Run all cloud tests and verify they pass**

Run:

```powershell
python -m unittest tests.test_trae
```

Expected: all existing and new tests pass with exit code 0。

- [ ] **Step 5: Commit the cloud signer change**

```powershell
git add main.py signers/base.py signers/trae.py tests/test_trae.py
git commit -m "feat: add TRAE checkin diagnostics and account filter"
```

### Task 2: GitHub Actions 单账号输入与并发保护

**Files:**
- Modify: `云签到/.github/workflows/daily-checkin.yml`
- Modify: `云签到/tests/test_trae.py`

**Interfaces:**
- `workflow_dispatch.inputs.account_filter` 是可选字符串，默认空值。
- 运行步骤将输入传给 `CHECKIN_ACCOUNT_FILTER`。
- `concurrency.group` 固定为签到 workflow 组，`cancel-in-progress: false`。

- [ ] **Step 1: Write failing workflow assertions**

在现有 workflow 测试中断言存在：

```python
self.assertIn("account_filter:", workflow)
self.assertIn("CHECKIN_ACCOUNT_FILTER:", workflow)
self.assertIn("concurrency:", workflow)
self.assertIn("cancel-in-progress: false", workflow)
```

- [ ] **Step 2: Run the test and verify it fails**

```powershell
python -m unittest tests.test_trae.HardeningTests.test_workflow_supports_single_account_and_concurrency
```

Expected: assertion failure because the current workflow has no input/concurrency block。

- [ ] **Step 3: Add the workflow configuration**

在 `on.workflow_dispatch` 增加可选输入；在 `on` 后增加：

```yaml
concurrency:
  group: daily-checkin
  cancel-in-progress: false
```

在 `Run checkin` 的 `env` 增加：

```yaml
CHECKIN_ACCOUNT_FILTER: ${{ inputs.account_filter }}
```

- [ ] **Step 4: Run the workflow assertions and full Python tests**

```powershell
python -m unittest tests.test_trae
```

Expected: all tests pass。

- [ ] **Step 5: Commit the workflow change**

```powershell
git add .github/workflows/daily-checkin.yml tests/test_trae.py
git commit -m "ci: support isolated TRAE checkin runs"
```

### Task 3: 本地 Rust 诊断结果模型与流程

**Files:**
- Modify: `切换应用/source/src-tauri/src/models/work_cn.rs`
- Modify: `切换应用/source/src-tauri/src/modules/trae_account.rs`
- Test: `切换应用/source/src-tauri/src/modules/trae_account.rs`（现有 `#[cfg(test)]` 模块）

**Interfaces:**
- `WorkCnLocalCheckinResult` 增加 `outcome`, `stage`, `business_code`, `http_status`, `claim_attempted`, `token_expiry_state`, `device_present` 字段。
- `outcome` 只允许 `already_checked_in`、`claimed`、`failed` 三种字符串。
- `request_trae_checkin_json` 返回 HTTP 状态和 JSON 数据，以便 `status`/`claim` 失败保留业务码。

- [ ] **Step 1: Write failing Rust tests**

添加测试断言：

```rust
assert_eq!(result.outcome, "already_checked_in");
assert!(!result.claim_attempted);
assert_eq!(result.outcome, "failed");
assert_eq!(result.stage, "claim");
assert_eq!(result.business_code, Some(500));
```

- [ ] **Step 2: Run focused Rust tests and verify failure**

```powershell
cd 'C:\Users\10156\Desktop\脚本\切换应用\source'
cargo test parse_checkin_business_code --manifest-path src-tauri/Cargo.toml
```

Expected: compile/test failure because the result model lacks the new fields。

- [ ] **Step 3: Implement the minimal model and flow changes**

在 `WorkCnLocalCheckinResult` 中使用 `Option<i64>` 保存业务码和 HTTP 状态，使用 `String` 保存阶段/结果枚举；所有返回分支显式填充诊断字段。已签到分支设置 `outcome=already_checked_in`、`stage=status`、`claim_attempted=false`；claim 成功设置 `outcome=claimed`；claim 业务失败设置 `outcome=failed`、`stage=claim`。

- [ ] **Step 4: Run Rust tests**

```powershell
cargo test --manifest-path src-tauri/Cargo.toml
```

Expected: exit code 0；若工作区已有与本任务无关的失败，记录具体失败测试，不修改无关模块。

- [ ] **Step 5: Commit the Rust change**

```powershell
git add src-tauri/src/models/work_cn.rs src-tauri/src/modules/trae_account.rs
git commit -m "feat: expose local TRAE checkin diagnostics"
```

### Task 4: 本地前端区分状态并验证类型

**Files:**
- Modify: `切换应用/source/src/types/checkin.ts`
- Modify: `切换应用/source/src/pages/CheckinPanelPage.tsx`
- Modify: `切换应用/source/src/stores/useCheckinStore.ts`（仅在类型不匹配时调整）
- Test: `切换应用/source` TypeScript typecheck

**Interfaces:**
- 前端类型与 Rust 返回字段完全一致。
- `already_checked_in` 显示“查询到今日已签到，本次未发起领取”。
- `claimed` 显示“本次领取成功”。
- `failed` 显示阶段和业务码，不显示原始凭证。

- [ ] **Step 1: Add a type-level failing assertion**

先在 `checkin.ts` 为 `LocalCheckinResult` 添加期望字段的使用点，并运行类型检查，确认当前 Rust/前端类型定义不完整导致失败。

- [ ] **Step 2: Run typecheck and verify failure**

```powershell
cd 'C:\Users\10156\Desktop\脚本\切换应用\source'
npm run typecheck
```

Expected: failure指出新增字段尚未定义。

- [ ] **Step 3: Implement safe result rendering**

更新 `LocalCheckinResult` 类型和页面分支渲染。只使用 `outcome`、`stage`、`business_code`、`http_status` 等安全字段；不要渲染后端 detail 中可能出现的完整请求内容。

- [ ] **Step 4: Run typecheck and relevant frontend tests**

```powershell
npm run typecheck
node --test src/utils/codexApiKeyAccountScope.test.ts
```

Expected: typecheck exit 0，现有 Node 测试进程 exit 0；不运行项目中不存在的通用 `npm test` 脚本。

- [ ] **Step 5: Commit the frontend change**

```powershell
git add src/types/checkin.ts src/pages/CheckinPanelPage.tsx src/stores/useCheckinStore.ts
git commit -m "feat: distinguish local checkin outcomes"
```

### Task 5: 集成验证与实验记录

**Files:**
- Test: `云签到/tests/test_trae.py` 和 `切换应用/source` 的现有测试/类型检查。

- [ ] **Step 1: Run complete local verification**

```powershell
cd 'C:\Users\10156\Desktop\脚本\云签到'
python -m unittest discover -s tests -v
cd 'C:\Users\10156\Desktop\脚本\切换应用\source'
cargo test --manifest-path src-tauri/Cargo.toml
npm run typecheck
```

- [ ] **Step 2: Trigger one isolated cloud run**

在 GitHub Actions 手动运行 `daily-checkin.yml`，输入一个尚未签到的账号，记录 `status`/`claim` 阶段、HTTP 状态和业务码，不复制任何凭证。

- [ ] **Step 3: Run one local comparison**

对同一个账号执行一次本地签到，记录相同字段并标注账号是否已在官方客户端登录但未领取。

- [ ] **Step 4: Compare results and decide next scope**

仅根据证据决定下一步：本地成功而云端失败时研究云端网络/IP；本地和云端都失败而官方客户端成功时研究 Cookie/设备签名；两者都返回 401 时更新凭证。不要在没有这组结果前增加认证字段或重试。

- [ ] **Step 5: Commit only verified fixes**

```powershell
git status --short
git diff --check
```

只提交本计划产生的文件，保留工作区中与本任务无关的用户修改。
