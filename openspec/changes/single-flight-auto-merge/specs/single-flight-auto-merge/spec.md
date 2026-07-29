## ADDED Requirements

### Requirement: Per-repository serial single-flight consumption
The dispatch stage SHALL consume admitted PRDs for a single target repository strictly serially — at most one PRD at a time MAY be inside the dev→verify→merge closed loop for a given owner_repo — while different target repositories progress independently without blocking on one another. The single-flight slot state SHALL be persisted in the journal and guarded by a cross-process lock (not an in-process lock), so that it survives dispatch-process restarts across cron runs.

#### Scenario: Same repo second PRD waits
- **WHEN** a PRD for owner_repo X is already inside the dev→verify→merge loop
- **THEN** no second PRD for X enters the loop until the first completes (merged, reverted, or ejected to triage)

#### Scenario: Different repos do not block each other
- **THEN** neither of two admitted PRDs for different owner_repos blocks waiting on the other; each records an independent dispatch-start event

#### Scenario: Slot state recovered after crash
- **WHEN** the dispatch process restarts after crashing mid-loop and the journal is replayed
- **THEN** the single-flight slot for the affected owner_repo resolves to a known state (free, or in-flight-with-lease under a timeout) before any new PRD for that repo is admitted — it MUST NOT default to free

### Requirement: Automated merge on verified green
When development and verification both pass (green) for a PRD, the dispatch stage SHALL rebase the branch onto the current main and, when the rebase is clean, merge it into main with `--no-ff` (producing a single merge commit) and push fast-forward only — in place of opening a pending-review pull request. The merge, push, revert, and post-merge test operations SHALL be performed via the dev-agent SDK loop inside the target worktree, not by the control plane holding a direct git write handle. The pending-review PR path is removed by this change; every PRD that is not auto-merged is routed to the triage pool.

#### Scenario: Verified green and clean rebase
- **WHEN** dev and verify pass and rebasing the branch onto main produces no conflict
- **THEN** dispatch merges the branch into main with `--no-ff`, pushes fast-forward only, and records the single merge commit sha; no pending-review PR is opened

#### Scenario: Verification not green
- **WHEN** verify is red or has not reached a pass verdict
- **THEN** dispatch performs no merge and ejects the PRD to the triage pool

#### Scenario: Push of the merge commit fails
- **WHEN** pushing the merge commit is rejected (non-fast-forward, missing authentication, or network failure)
- **THEN** the outcome is treated as `UNKNOWN`, the PRD is ejected to triage, main is left unchanged, and the slot is released; dispatch MUST NOT force-push

### Requirement: Three-state rebase safety before merge
The pre-merge rebase MUST resolve to an explicit `CLEAN`, `CONFLICT`, or `UNKNOWN` outcome. Only `CLEAN` triggers automatic merge; `CONFLICT` and `UNKNOWN` MUST route the PRD to the triage pool and MUST NOT force-merge. `CLEAN` MUST be asserted only on positive evidence — a successful fetch of main's HEAD, rebase exit code zero, a clean working tree, and no conflict markers; absence of positive evidence MUST yield `UNKNOWN`, never `CLEAN`.

#### Scenario: Rebase conflict routes to triage
- **WHEN** rebasing onto main reports an explicit conflict
- **THEN** the branch is not merged, the PRD is ejected to the triage pool, and main is left unchanged

#### Scenario: Rebase state unknown blocks merge
- **WHEN** the rebase outcome cannot be established (command failure, timeout, or missing authentication)
- **THEN** the PRD is ejected to triage and no merge occurs

#### Scenario: No positive evidence is not clean
- **WHEN** the rebase command was killed by timeout leaving a half-completed state, or the fetch of main failed
- **THEN** the outcome is `UNKNOWN` even if no conflict markers are present; the PRD is not merged

### Requirement: Post-merge main verification and auto-revert
After an automated merge, the dispatch stage SHALL run the repository's full test suite against the integrated main (whose baseline differs from the candidate branch used during verify, so this suite is not a duplicate of verify). The result SHALL be three-state `PASS`/`FAIL`/`UNKNOWN`. On `FAIL`, dispatch SHALL revert the single merge commit (whose sha was recorded at merge); the sha of the revert commit produced SHALL be recorded for exactly-once reconciliation, and the revert itself SHALL be three-state `REVERTED`/`CONFLICT`/`UNKNOWN`. Only `REVERTED` releases the slot and continues the queue. Any non-`REVERTED` revert outcome, and any `UNKNOWN` test result, SHALL halt the queue for that owner_repo and raise a CRITICAL alert — dispatch MUST NOT continue to the next PRD on a failed or uncertain revert.

#### Scenario: Main stays green after merge
- **WHEN** the post-merge full test suite on main returns `PASS`
- **THEN** the merge is kept, the slot is released, and the queue proceeds to the next PRD

#### Scenario: Main goes red and revert succeeds
- **WHEN** the post-merge suite returns `FAIL` and reverting the single merge commit returns `REVERTED`
- **THEN** the merge commit is reverted, the reverted PRD is ejected to the triage pool with reason `post_merge_red_reverted`, the slot is released, and the queue proceeds

#### Scenario: Revert itself fails halts the queue
- **WHEN** reverting the merge commit returns `CONFLICT` or `UNKNOWN`
- **THEN** the queue for that owner_repo halts, a CRITICAL alert is raised, and no further PRD is admitted until manual resolution; main is not force-modified

#### Scenario: Post-merge test result unknown halts the queue
- **WHEN** the post-merge suite returns `UNKNOWN` (timeout, crash, or environment failure)
- **THEN** the merge is neither auto-reverted nor declared kept; the PRD is ejected with reason `post_merge_unknown`, the queue halts, and a CRITICAL alert is raised

### Requirement: Non-blocking triage pool
PRDs that time out, fail verification after the configured retries, hit a rebase `CONFLICT`/`UNKNOWN`, encounter a merge-push failure, or are reverted or left unknown by post-merge verification SHALL be ejected to a triage pool without blocking consumption of subsequent PRDs — except when the queue is explicitly halted by the auto-revert rules above. Triaged PRDs SHALL be reported distinctly from merged and in-flight work, with an ejection reason drawn from a fixed enumeration (`timeout` / `verify_exhausted` / `rebase_conflict` / `rebase_unknown` / `push_failed` / `post_merge_red_reverted` / `post_merge_unknown`).

#### Scenario: Failed PRD does not block the queue
- **WHEN** a PRD is ejected to triage for a non-halting reason
- **THEN** the next queued PRD for that repository proceeds without waiting for manual resolution of the ejected one

#### Scenario: Triage is reported separately
- **WHEN** the daily report is produced
- **THEN** triaged PRDs are listed with their enumerated ejection reason, distinct from merged and in-flight work

### Requirement: Revert loop circuit breaker
A PRD whose idempotency key matches a `post_merge_red_reverted` ejection within the configured cooldown window SHALL be blocked from auto-merge on re-admission across cron rounds, and routed to the triage pool — to prevent a PRD that is green on its branch but red on integrated main from being re-merged nightly in an infinite loop.

#### Scenario: Reverted PRD re-admitted inside cooldown
- **WHEN** a PRD whose idempotency key was `post_merge_red_reverted` within the cooldown window is re-admitted
- **THEN** dispatch blocks auto-merge for that PRD and routes it to the triage pool

#### Scenario: Reverted PRD re-admitted after cooldown
- **WHEN** the cooldown window has elapsed
- **THEN** the PRD MAY be re-admitted to the normal single-flight flow
