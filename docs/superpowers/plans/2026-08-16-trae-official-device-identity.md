# TRAE Official Device Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local and GitHub Actions TRAE check-in requests use the same numeric registered device identity and device headers as TRAE Work, while separating Token rotation from valid-token device identity repair.

**Architecture:** Keep account storage backward-compatible, but introduce narrow pure helpers that select the official numeric `auth_device_id`, build device headers, compare token metadata, and validate a complete target snapshot. The switcher writes existing token/device secrets plus derived brand/type secrets; the Python signer consumes them without retries. Refresh orchestration waits for Token rotation only when the cached Token is expired or due for rotation; valid Tokens can synchronize a newly verified device snapshot without a false rotation claim.

**Tech Stack:** Rust/Tauri 2, TypeScript/React/Zustand, Python 3 unittest/requests, GitHub Actions, NSIS.

## Global Constraints

- Never log or expose access tokens, private keys, or complete device identifiers.
- Do not retry a TRAE business failure.
- Do not fall back from numeric `auth_device_id` to UUID `checkin_device_id` for check-in.
- Do not impersonate TTNet or bypass server-side daily-device policy.
- Preserve current GitHub `<STEM>_TOKEN` and `<STEM>_DEVICE_ID` names.

---

### Task 1: Select Official Device Identity For Local Check-In

**Files:**
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/src/modules/trae_account.rs`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/src/modules/trae_oauth.rs`

**Interfaces:**
- Produces: `resolve_official_checkin_device_id(&TraeAccount) -> Result<&str, String>`
- Produces: `official_checkin_device_headers(TraePlatformKind) -> (String, String)` returning `(brand, type)`.
- Consumes: saved `auth_device_id`, existing log-based device-brand detection, and OS detection.

- [ ] **Step 1: Add failing Rust tests**

Add tests proving a numeric `auth_device_id` is selected even when a UUID `checkin_device_id` exists, and that UUID/missing auth IDs are rejected.

- [ ] **Step 2: Run the focused tests and observe the expected failure**

Run: `cargo test resolve_official_checkin_device_id --manifest-path src-tauri/Cargo.toml`

Expected: compilation/test failure because the resolver does not exist.

- [ ] **Step 3: Implement the resolver and official header context**

Use trimmed ASCII-digit validation. Expose a crate-private wrapper around the existing `detect_device_brand` and `detect_device_type`; do not duplicate platform detection.

- [ ] **Step 4: Replace the local request shape**

Build explicit headers matching the official client: `Content-Type`, `Authorization`, `x-device-id`, optional `x-device-brand`, and optional `x-device-type`. Remove explicit `Origin`, `Referer`, and custom `Trae/1.0.0` User-Agent.

- [ ] **Step 5: Run focused Rust tests**

Run: `cargo test resolve_official_checkin_device_id --manifest-path src-tauri/Cargo.toml`

Expected: all matching tests pass.

### Task 2: Sync Official Device Context To GitHub Secrets

**Files:**
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/src/modules/work_cn_github.rs`

**Interfaces:**
- Consumes: `resolve_official_checkin_device_id` and `official_checkin_device_headers` from Task 1.
- Produces: `<STEM>_DEVICE_ID`, `<STEM>_DEVICE_BRAND`, and `<STEM>_DEVICE_TYPE` secret updates through stdin.

- [ ] **Step 1: Change the existing fake-runner test first**

Require four secret-set calls total (token, numeric device ID, brand, type), assert no secret value appears in CLI arguments, and assert UUID-only accounts are skipped.

- [ ] **Step 2: Run the focused test and observe failure**

Run: `cargo test work_cn_github_sync_calls --manifest-path src-tauri/Cargo.toml`

Expected: failure because only two secrets are currently written and the UUID field is selected.

- [ ] **Step 3: Implement deterministic context injection and production wrapper**

Keep `sync_account_secrets` as the production entry. Add a private context-aware helper used by tests so fake brand/type values are deterministic. Derive brand/type names from the same slot stem and send all values via stdin.

- [ ] **Step 4: Update missing-device diagnostics and duplicate-name validation**

Use the message `缺少数字 auth_device_id，跳过同步`; validate the derived brand/type secret names alongside token/device names.

- [ ] **Step 5: Run all `work_cn_github` tests**

Run: `cargo test work_cn_github --manifest-path src-tauri/Cargo.toml`

Expected: all matching tests pass.

### Task 3: Separate Token Rotation From Device Identity Refresh

**Files:**
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/src/models/work_cn.rs`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/src/modules/trae_account.rs`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src/types/workCn.ts`
- Create: `C:/Users/10156/Desktop/脚本/切换应用/source/src/utils/tokenRotation.ts`
- Create: `C:/Users/10156/Desktop/脚本/切换应用/source/src/utils/tokenRotation.test.ts`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src/stores/useCheckinStore.ts`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/package.json`

**Interfaces:**
- Produces: desensitized `tokenIssuedAt: number | null` on `WorkCnAccountView`.
- Produces: `didTokenRotate(before, after): boolean`, comparing `iat` and `exp` metadata.
- Produces: `shouldWaitForTokenRotation(metadata, now): boolean` and a complete-snapshot predicate used before GitHub synchronization.
- Consumes: session watcher `TOKEN_UPDATED` event and refreshed account list.

- [ ] **Step 1: Add failing TypeScript unit tests**

Test unchanged `iat/exp` returns false, changed `iat` or `exp` returns true, missing metadata does not create a false positive, expired metadata requires rotation, and a valid unrotated Token does not require rotation when the device snapshot is complete.

- [ ] **Step 2: Run the test and observe failure**

Run: `node --test src/utils/tokenRotation.test.ts`

Expected: module/function missing failure.

- [ ] **Step 3: Implement metadata exposure, rotation policy, and snapshot predicate**

Parse JWT `iat` in Rust exactly as `exp` is parsed, expose only timestamps, implement pure TypeScript helpers for rotation policy and complete snapshot validation, and add `test:token-rotation` to package scripts.

- [ ] **Step 4: Replace the five-day freshness shortcut and split refresh paths**

Capture the pre-switch UID, Token metadata, and device snapshot. Confirm the switched client belongs to the requested UID and has a valid complete device snapshot. If the Token is expired/due for rotation, finish only on the matching `TOKEN_UPDATED` event or when polling observes `didTokenRotate(...) === true`; if it remains valid, synchronize immediately after snapshot validation. On timeout report Token rotation failure only for the expired/due path.

- [ ] **Step 5: Run TypeScript tests and typecheck**

Run: `npm run test:token-rotation` and `npm run typecheck`

Expected: tests and typecheck pass.

### Task 4: Consume Official Device Headers In Cloud Signer

**Files:**
- Modify: `C:/Users/10156/Desktop/脚本/云签到/common/config.py`
- Modify: `C:/Users/10156/Desktop/脚本/云签到/config.yaml`
- Modify: `C:/Users/10156/Desktop/脚本/云签到/signers/trae.py`
- Modify: `C:/Users/10156/Desktop/脚本/云签到/.github/workflows/daily-checkin.yml`
- Modify: `C:/Users/10156/Desktop/脚本/云签到/tests/test_trae.py`

**Interfaces:**
- Produces: optional `device_brand_env` and `device_type_env` account config fields.
- Consumes: `<STEM>_DEVICE_ID`, `<STEM>_DEVICE_BRAND`, and `<STEM>_DEVICE_TYPE` environment values.

- [ ] **Step 1: Add failing Python tests**

Require numeric device IDs; verify `_headers` sends brand/type only when non-empty; verify workflow env contains brand/type secrets for every configured TRAE account.

- [ ] **Step 2: Run focused tests and observe failure**

Run: `python -m unittest tests.test_trae -v`

Expected: new assertions fail because config fields and headers do not exist.

- [ ] **Step 3: Implement config and signer changes**

Load the two optional env names, validate the required device ID as digits, carry brand/type in the auth dictionary, and add non-empty headers. Keep business-failure behavior unchanged.

- [ ] **Step 4: Update workflow environment wiring**

Map each configured secret to its matching environment name without printing secret values.

- [ ] **Step 5: Run the full Python suite**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 5: Version, Cross-Project Verification, And Installer

**Files:**
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/package.json`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/package-lock.json`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/Cargo.toml`
- Modify: `C:/Users/10156/Desktop/脚本/切换应用/source/src-tauri/tauri.conf.json`

**Interfaces:**
- Produces: Windows x64 NSIS installer version `0.1.2`.

- [ ] **Step 1: Bump all switcher version sources to `0.1.2`**

Use the repository version-sync script and verify all four files agree.

- [ ] **Step 2: Run complete cloud verification**

Run: `python -m unittest discover -s tests -v` in `云签到`.

Expected: zero failures.

- [ ] **Step 3: Run complete switcher preflight**

Run: `npm run release:preflight` in `切换应用/source`.

Expected: locale consistency, typecheck, frontend build, Rust check, and Rust tests all pass.

- [ ] **Step 4: Request focused code review and resolve findings**

Review the working-tree diff against this plan, paying special attention to device secrecy, fallback behavior, and false refresh success.

- [ ] **Step 5: Build and inspect the installer**

Run: `npm run tauri build`.

Expected: `target/release/bundle/nsis/切换工具_0.1.2_x64-setup.exe` exists; record SHA256, size, version, and signature status.

- [ ] **Step 6: Online behavior check without repeated claims**

For an account already rejected by the official client with `9095`, run at most one local check-in after installing `0.1.2`. Expected diagnostic: official-aligned device identity, with `9095` rather than UUID-context `9074`. Leave successful unclaimed-account verification for the next calendar day.
