## MODIFIED Requirements

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

## ADDED Requirements

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
