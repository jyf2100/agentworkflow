# fail-safe-dispatch

## Purpose

Define how project-pipeline dispatch behaves when the source of truth (GitHub or Git remote) cannot be reliably queried. Replace implicit "treat unknown as not-found" semantics with explicit three-state outcomes so that dispatch admission, idempotency, in-flight limits, and reconciliation fail safe — blocking work and preserving remote state rather than over-asserting or destructively reconciling.

## Requirements

### Requirement: Explicit external lookup outcome
Every dispatch-critical Git-hosting-platform or Git remote lookup MUST return an explicit `FOUND`, `NOT_FOUND`, or `UNKNOWN` outcome with diagnostic context. 平台覆盖由 forge-adapter capability 声明（github / gitlab / gitcode）；三态判据是 adapter 契约的一部分，dispatch 层 MUST NOT 绕过 adapter 自行解析平台响应。REST 分支的配置类确定性失败（HTTP 401/403）不属三态：dispatch 消费面 MUST 将其映射为单项目终态（skip + 高声暴露），不进瞬态重试臂、不影响同批其他项目。

#### Scenario: Valid empty query response
- **WHEN** the external command or API call succeeds and returns a valid empty result
- **THEN** the lookup returns `FOUND` with an empty result; dispatch consumes FOUND-empty per call-site semantics（`_lookup_pr` 空 = 无既有 PR，按无 PR 处理；`count_inflight_prs` 空 = 0 在途）——映射按调用点既有规则完成，adapter 恒返回 FOUND-空、不感知调用语境

#### Scenario: External command cannot establish state
- **WHEN** the command or API call times out, exits non-zero or returns non-2xx, lacks authentication（gh 形态凭证缺失）, is rate-limited (429), is unavailable, or returns invalid data
- **THEN** the lookup returns `UNKNOWN` and records a sanitized reason（REST 分支的 401/403 除外——按上方配置类失败条款处理）

#### Scenario: REST 认证失败是单项目终态而非 UNKNOWN
- **WHEN** gitlab/gitcode 调用收到 HTTP 401/403（token 无效 / 权限不足）
- **THEN** 该项目按配置类确定性失败 skip + 高声暴露（不重试、不炸整个 dispatch 段）；gh 分支的凭证缺失仍走 `UNKNOWN`

#### Scenario: Non-GitHub platform gains identical guarantees
- **WHEN** the target repository is hosted on gitlab (local instance) or gitcode
- **THEN** every dispatch-critical remote lookup returns the same explicit three-outcome contract with the same fail-safe semantics as the github path (UNKNOWN never treated as success)

### Requirement: Fail-safe dispatch admission
Dispatch SHALL start a development agent for a target repository only when branch protection, idempotency state, and the per-repository single-flight slot are all known and satisfy their admission rules; admission for a repository SHALL be serial (at most one in-flight dev→verify→merge loop per repository), and an unknown single-flight slot or unknown external state MUST block rather than be treated as idle. The single-flight slot state is persisted in the journal and guarded cross-process so that it survives dispatch-process restarts across cron runs.

#### Scenario: Idempotency state is unknown
- **WHEN** existing pull requests and remote branches cannot be queried reliably
- **THEN** dispatch records `blocked_external_state` and does not start the development agent

#### Scenario: Single-flight slot is unknown
- **WHEN** the per-repository in-flight loop state cannot be established
- **THEN** dispatch records `blocked_external_state` instead of treating the slot as free

#### Scenario: Slot recovered to known after restart
- **WHEN** the dispatch process restarts and the journal is replayed
- **THEN** the single-flight slot resolves to a known state (free, or in-flight-under-lease with a timeout) before any new PRD for that repo is admitted; it MUST NOT default to free

#### Scenario: All admission checks are known and pass
- **WHEN** protection is enabled, no matching dispatch exists, and the repository has no in-flight single-flight loop
- **THEN** dispatch MAY create the worktree and invoke the development agent

### Requirement: Non-destructive reconciliation under uncertainty
Reconciliation MUST NOT delete branches, create replacement pull requests, or overwrite known publication state when GitHub or Git cannot establish the current remote state.

#### Scenario: Pull-request lookup fails after development
- **WHEN** a development branch exists but the pull-request query returns `UNKNOWN`
- **THEN** reconciliation preserves the branch and records a recoverable blocked result without creating or deleting remote objects

#### Scenario: Remote state is known
- **WHEN** pull-request and commit lookups complete successfully
- **THEN** reconciliation applies the existing three-state behavior for existing PR, commit without PR, or branch without commit

### Requirement: Visible recoverable blocking
Reports and persisted dispatch records MUST distinguish external-state blocking from skipped, failed, test-failing, and successfully dispatched work.

#### Scenario: Daily report contains blocked work
- **WHEN** one or more PRDs were not dispatched because an external lookup was unknown
- **THEN** the report lists each blocked PRD, failed check, first occurrence, and whether a manual or later retry is permitted

### Requirement: Exactly-once reconciliation of merge and revert side effects
Automated merge and auto-revert are destructive, cross-cron side effects on the target repository's main. They MUST be reconciled exactly-once: each is recorded as an idempotent journal event of kind `merge` or `revert` (extending the reconciler's allowed kinds), and on recovery the reconciler resolves whether the side effect already occurred using a positive-evidence ancestry check that returns FOUND / NOT_FOUND / UNKNOWN: for a `merge` event, whether the recorded merge commit is an ancestor of main; for a `revert` event, whether the recorded revert commit (the new commit `git revert` produced, whose sha MUST be captured at revert time) is an ancestor of main. Because `git revert` appends a new commit without removing the original merge commit, the original merge commit remains an ancestor of main even after a successful revert; therefore revert reconciliation MUST key on the revert commit's ancestry and MUST NOT infer revert state from the presence or absence of the merge commit. A reconciled-as-FOUND merge MUST NOT be re-merged; a reconciled-as-UNKNOWN side effect MUST block rather than be retried optimistically. The write order MUST be: append an intent event, perform the push, then append a confirm event — so a crash between intent and confirm yields a resolvable state, never a silent double-apply or lost write. The dispatch stage MUST define new crash boundaries (`merge_push`, `revert_push`) so recovery can resume at the correct sub-step rather than re-running the whole closed loop.

#### Scenario: Merge already applied is not repeated
- **WHEN** recovery replays a `merge` intent whose target commit is already an ancestor of main (FOUND)
- **THEN** the reconciler skips re-merging and records the merge as completed

#### Scenario: Revert already applied is detected via the revert commit
- **WHEN** recovery replays a `revert` intent and the recorded revert commit is an ancestor of main (FOUND)
- **THEN** the reconciler skips re-reverting; it MUST NOT key revert state on the merge commit, which remains an ancestor of main even after a successful revert

#### Scenario: Merge state unknown blocks retry
- **WHEN** the ancestry check for a `merge` intent returns UNKNOWN (remote unreachable or command failure)
- **THEN** the reconciler blocks rather than re-applying the merge, and raises an alert

#### Scenario: Crash between intent and confirm is resolvable
- **WHEN** a crash occurs after the intent event but before the confirm event for a merge or revert
- **THEN** the reconciler resolves the actual state via the ancestry check and either completes or blocks; it does not silently apply twice nor lose the side effect
