## 1. Reproducible Engineering Baseline

- [x] 1.1 Add `pyproject.toml` with supported Python versions, runtime dependencies, development dependencies, pytest configuration, and ruff configuration.
- [x] 1.2 Add a single documented local quality command that runs Python compilation, the complete pytest suite, and ruff.
- [x] 1.3 Fix the clean-environment dependency gap so all existing inject and verify-loop tests pass without manual package installation.
- [x] 1.4 Add CI for pull requests and main-branch pushes that installs the declared environment and runs the same quality command.

## 2. Verified Development Execution

- [x] 2.1 Add unit tests for no test run, failed test, green test, and candidate changes made after a green test.
- [x] 2.2 Introduce structured test evidence containing the test command, exit status, completion point, and freshness binding to candidate changes.
- [x] 2.3 Invalidate existing green evidence whenever the development loop performs a subsequent write affecting candidate files.
- [x] 2.4 Add a mechanical pre-publication gate that prevents commit, push, and PR creation unless test evidence is fresh and green.
- [x] 2.5 Extend final executor JSON and PR text to report only truthful test state and precise blocked reasons.

## 3. Control-plane Standard Executor Migration

- [x] 3.1 Add Node and Python temporary target-repository fixtures that isolate SDK, GitHub, SMTP, credentials, and model calls.
- [x] 3.2 Change dispatch command construction to invoke the control-plane `scripts/dev-agent.py` with the target worktree as cwd, regardless of target-repository language.
- [x] 3.3 Replace target-repository script existence admission with explicit profile readiness and runtime validation while retaining backward-compatible profile reads.
- [x] 3.4 Make branch slug generation a single importable implementation and remove the duplicate implementation from the orchestrator.
- [x] 3.5 Add tests proving repositories with no local executor work and legacy local executors are ignored.

## 4. Fail-safe External State Handling

- [x] 4.1 Define a typed three-state external lookup result with sanitized diagnostic context.
- [x] 4.2 Convert branch-protection, idempotency, remote-branch, open-PR-count, PR lookup, and commit lookup helpers to distinguish known absence from unknown state.
- [x] 4.3 Change dispatch admission to record `blocked_external_state` and avoid starting the dev agent when any critical lookup is unknown.
- [x] 4.4 Change reconciliation to preserve branches and avoid PR creation, deletion, or state overwrite when remote state is unknown.
- [x] 4.5 Add timeout, non-zero exit, missing command, authentication failure, and invalid JSON tests for all dispatch-critical lookups.

## 5. State, Reporting, and Documentation

- [x] 5.1 Extend dispatch state records with optional blocked reason, external check evidence, and test evidence while keeping historical JSON fixtures readable.
- [x] 5.2 Add report sections for external-state blocking and test-gate blocking without counting either as successfully dispatched or verification green.
- [x] 5.3 Update ADR-0001, ADR-0003, ADR-0006, `CONTEXT.md`, and Linux deployment documentation to describe the final executor ownership and runtime model.
- [x] 5.4 Document retry semantics for blocked PRDs and the operator actions required for authentication or remote-service failures.

## 6. Verification and Rollout

- [x] 6.1 Run the complete local quality command and retain passing output for compilation, tests, and lint.
- [x] 6.2 Run a `--dispatch-skip-dev` smoke test covering profile load, external admission checks, and blocked-state reporting without remote writes.
- [x] 6.3 Perform one real-project canary using the control-plane executor and verify branch, test evidence, independent verification, reconciliation, and report output.
- [x] 6.4 Confirm rollback compatibility by reading newly written state with optional fields absent and by preserving all remote objects during simulated lookup failures.
