## 1. Restore a Trustworthy Baseline

- [x] 1.1 Remove the current Ruff failures and make `scripts/quality.sh` pass under the declared Python 3.11+ runtime.
- [x] 1.2 Add a quality-evidence command that records interpreter version, exact command, test counts, Ruff result, timestamp, and artifact digests from the real process result.
- [x] 1.3 Add regression tests for tests-green/semantic-revise, complete malformed journal tail, failed test-artifact persistence, and every previously unmapped terminal state.

## 2. Production Runtime Coordinator

- [x] 2.1 Implement a coordinator that resolves all loop flags once and owns journal, artifacts, IDs, retry, hooks, sandbox, telemetry, and reconciliation for one dispatch.
- [x] 2.2 Integrate the coordinator into `run_daily.dispatch_one()` without changing first-phase admission and fail-safe external-state gates when durable flags are disabled.
- [x] 2.3 Integrate the coordinator into `dev-agent.py` and register real Claude Agent SDK lifecycle hooks with the pinned SDK API.
- [x] 2.4 Add subprocess-level integration tests proving each feature flag changes the real dispatch/SDK path rather than only a helper or drill function.
- [x] 2.5 Reject invalid partial feature combinations during preflight and record a structured blocked reason.

## 3. Immutable Iterations and Journal Authority

- [x] 3.1 Capture the immutable PRD content digest at dispatch entry and include it in `prd_id` and the initial journal event.
- [x] 3.2 Stop appending verifier feedback to PRDs for new runs; store feedback as sanitized content-addressed artifacts.
- [x] 3.3 Allocate a distinct deterministic iteration ID for every revise, resume, fork, and new-session attempt and record parent relationships.
- [x] 3.4 Build each retry prompt from immutable PRD content plus verified journal artifacts and remaining acceptance criteria.
- [x] 3.5 Emit and reduce all terminal classes, including planned smoke, stalled, orphan deletion, interrupted semantic revise, blocked evidence, and sandbox blocked.
- [x] 3.6 Make complete schema-invalid final journal records fail closed while continuing to tolerate only provably incomplete trailing writes.

## 4. Publication and Evidence Integrity

- [x] 4.1 Change terminal publication logic to require independent green tests, `verify_verdict=pass`, and known reconciliation.
- [x] 4.2 Prevent a test result from becoming fresh green evidence when its required artifact or journal event cannot be persisted and verified.
- [x] 4.3 Add explicit evidence-integrity and journal-integrity blocked states to reports and retry policy inputs.
- [x] 4.4 Reconcile commit, push, branch, PR, and test evidence idempotency keys before every resume, fork, new-session, or publication action.
- [ ] 4.5 Add crash-injection integration tests around each journal-before-side-effect boundary and prove exactly-once effective behavior.

## 5. Enforced Sandbox and Credential Boundary

- [ ] 5.1 Define the approved host/container egress enforcement adapter and preflight its availability before claiming higher assurance.
- [ ] 5.2 Replace label-only domain intent with enforceable egress policy, and return `sandbox_blocked` when policy installation or verification fails.
- [ ] 5.3 Route real dev and independent-test commands through the selected sandbox adapter and forbid silent fallback from container to local.
- [ ] 5.4 Keep GitHub, SMTP, cloud, and model publication credentials host-side and prove they are absent from sandbox environment and artifacts.
- [ ] 5.5 Add real Node and Python fixture canaries covering allowed network, denied network, denied credential access, resource limits, and unavailable runtime behavior.

## 6. Telemetry and Reporting Integration

- [ ] 6.1 Create a root trace per PRD run and propagate trace context through iteration, SDK session, tool, test, verify, reconcile, and publish operations.
- [ ] 6.2 Connect OTLP export and degradation journaling to the production coordinator with bounded timeouts and metadata-only attributes.
- [ ] 6.3 Extend reports with journal authority, trace ID, assurance tier, recovery mode, semantic verdict, evidence integrity, compaction count, and observability degradation.
- [ ] 6.4 Add production-path tests for OTLP outage, secret rejection, recovery span links, and report redaction.

## 7. Real Cutover and Recovery Drills

- [ ] 7.1 Run shadow parity against historical fixtures and one real no-write dispatch, resolving every terminal mismatch.
- [ ] 7.2 Run a real SDK hook canary for no-test, stale-test, green-test, semantic-revise, compaction, subagent, and hook-failure paths.
- [ ] 7.3 Run crash drills after agent completion, test completion, commit, push, and PR creation, and archive reconciliation evidence.
- [ ] 7.4 Implement the runbook's journal-corruption recovery command and test every documented command end to end.
- [ ] 7.5 Enable journal-driven dispatch for one allowlisted project only after parity passes; retain and test legacy read fallback for one release cycle.
- [ ] 7.6 Run the complete quality, sandbox, recovery, telemetry, and canary suite and archive immutable passing evidence before marking the change complete.
