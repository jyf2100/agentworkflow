## 1. Phase-One Readiness and Contracts

- [x] 1.1 Verify every `harden-project-pipeline` task is complete and its TestEvidence, external lookup, and executor contracts are covered by passing tests.
- [x] 1.2 Pin and document the Claude Agent SDK version used by the control-plane executor and add contract tests for required ResultMessage fields and lifecycle hooks.
- [x] 1.3 Add feature flags for journal shadow mode, journal-driven dispatch, session-aware retry, lifecycle hooks, container sandbox, and telemetry export.

## 2. Durable Journal and Artifact Store

- [x] 2.1 Define versioned journal event, iteration state, artifact reference, failure classification, and recovery snapshot data models.
- [x] 2.2 Implement atomic append, flush, read, validation, and reduction for per-run JSONL journals.
- [x] 2.3 Implement incomplete-tail recovery and fail-closed middle-corruption detection with focused tests.
- [x] 2.4 Implement a SHA-256 content-addressed artifact store for diffs, test output, verifier feedback, and recovery snapshots.
- [x] 2.5 Add artifact digest verification, metadata allowlists, secret redaction, and evidence-integrity failure tests.
- [x] 2.6 Implement the explicit iteration state machine and reject invalid or duplicate transitions.
- [x] 2.7 Add compatibility readers for historical dispatch JSON and report fixtures with no journal.

## 3. Shadow Journaling Integration

- [x] 3.1 Assign stable run, PRD, iteration, action, and idempotency IDs at dispatch entry.
- [x] 3.2 Emit planned/running/agent-finished/test/verifier/reconcile/publish journal events alongside the existing dispatch flow without changing decisions.
- [x] 3.3 Replace PRD feedback appends with journal feedback artifacts for new runs while preserving read compatibility for historical PRDs.
- [x] 3.4 Add a comparison command/test that proves journal-reduced terminal states match existing dispatch records in shadow mode.
- [x] 3.5 Add crash-injection tests at every side-effect boundary and verify recovery performs reconciliation before retry.

## 4. SDK Lifecycle Hooks and Evidence

- [x] 4.1 Implement a hook adapter that journals PreToolUse, PostToolUse, Stop, PreCompact, SubagentStart, and SubagentStop with stable correlation IDs.
- [x] 4.2 Move path, command, network, and protected-resource checks into deterministic PreToolUse policy components while retaining the first-phase permission gate.
- [x] 4.3 Pair PostToolUse results to tool-use IDs and persist structured exit status, changed paths, sanitized output artifacts, and TestEvidence updates.
- [x] 4.4 Implement a bounded Stop hook that blocks completion without fresh green TestEvidence and permits completion without replacing outer independent verification.
- [x] 4.5 Implement PreCompact recovery snapshots and block automatic recovery when a required snapshot cannot be persisted.
- [x] 4.6 Record subagent ownership, objective, tools, effort, status, and result artifact while preventing subagent publication actions.
- [x] 4.7 Add mock-SDK contract tests for denied tools, unpaired results, no-test Stop, stale-test Stop, compaction, hook failure, and subagent events.

## 5. Session-Aware Retry Policy

- [x] 5.1 Persist SDK session ID, ResultMessage subtype, stop reason, turns, usage, optional cost, compaction count, and exception classification for every iteration.
- [x] 5.2 Implement normalized failure fingerprints and progress signals based on diff/test/artifact hashes.
- [x] 5.3 Implement the versioned RetryPolicy with `resume`, `fork`, `new_session`, `block`, and `stop` decisions.
- [x] 5.4 Generate evidence-derived recovery context from immutable PRD content and journal artifacts.
- [x] 5.5 Reconcile branch, commit, PR, test, and publication idempotency keys before every retry mode.
- [x] 5.6 Enforce independent limits for Stop continuations, SDK retries, outer verify iterations, total wall-clock, turns, and trusted cost.
- [x] 5.7 Add tests for transient resume, verifier-driven resume, alternative fork, repeated-failure new session, missing session fallback, external-state block, and exhausted budget.

## 6. Execution Sandbox

- [x] 6.1 Define the `ExecutionSandbox` interface and implement the existing local-worktree behavior as an explicitly lower-assurance adapter.
- [x] 6.2 Implement a container adapter with non-root identity, writable worktree-only mount, read-only PRD/source mounts, temporary home, and CPU/memory/process limits.
- [x] 6.3 Add profile-driven network allowlists and tests proving undeclared destinations are blocked.
- [x] 6.4 Keep long-lived GitHub/SMTP/cloud credentials on the control-plane host and implement host-side verified publication.
- [x] 6.5 Make sandbox startup/policy failure produce `sandbox_blocked` without automatic fallback to local mode.
- [x] 6.6 Add Node and Python fixture repositories that run through both assurance tiers without real external services.

## 7. OpenTelemetry and Operational Metrics

- [ ] 7.1 Define metadata-only span and metric conventions for run, iteration, SDK session, tool, test, verify, reconcile, and publish.
- [ ] 7.2 Implement trace-context persistence and span links across resume, fork, subprocess, and cross-process recovery.
- [ ] 7.3 Add OTLP export with bounded timeouts and a local observability-degradation journal event when the backend is unavailable.
- [ ] 7.4 Implement metrics for success, blocked/failed states, iteration count, test pass rate, repeated failures, recovery success, cost, and wall-clock.
- [ ] 7.5 Add telemetry field-allowlist and secret-leak tests that reject prompts, source code, full tool output, credentials, cookies, and environment values.
- [ ] 7.6 Extend reports with trace IDs, assurance tier, recovery mode, compaction count, and observability degradation without exposing sensitive data.

## 8. Cutover, Canary, and Recovery Drills

- [ ] 8.1 Run shadow journaling against representative historical fixtures and one real dry-run, resolving every state mismatch.
- [ ] 8.2 Enable lifecycle hooks for one white-listed project and verify no-test, test-red, test-green, and compaction paths.
- [ ] 8.3 Run controlled crash drills after agent completion, test completion, push, and PR creation and confirm exactly-once effective behavior.
- [ ] 8.4 Canary session resume, fork, and new-session recovery using bounded model budget and verify journal causality.
- [ ] 8.5 Canary isolated-container execution for one Node and one Python project, including network and credential denial tests.
- [ ] 8.6 Enable journal-driven dispatch after shadow parity and retain legacy-state fallback for one release cycle.
- [ ] 8.7 Document operator recovery for state corruption, missing session, sandbox failure, telemetry outage, and externally blocked reconciliation.
- [ ] 8.8 Run the full repository quality command and archive passing test, sandbox, recovery, and telemetry evidence before declaring rollout complete.
