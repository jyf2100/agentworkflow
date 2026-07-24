## Context

The archived runtime change provides standalone journal, retry, hook, sandbox, telemetry, and cutover modules. The review of `cef8191` found that `run_daily.py` still executes the legacy dispatch path and only writes optional shadow events. New code must preserve first-phase fail-safe publication gates while making the durable runtime the source of decisions for new runs.

## Goals / Non-Goals

**Goals:**

- Integrate the existing runtime modules through explicit adapters at the real dispatch and SDK boundaries.
- Preserve a shadow mode and one-release legacy fallback while proving terminal-state parity.
- Make immutable PRD content, per-round iteration IDs, journal artifacts, and reconciliation the inputs to recovery.
- Prevent false `published` states and fail closed when journal, artifact, sandbox, or evidence integrity is unknown.
- Produce reproducible quality, real SDK/container canary, crash-drill, and operator-recovery evidence.

**Non-Goals:**

- Replacing Claude Agent SDK or introducing LangGraph.
- Redesigning radar, PRD, critic, or first-phase admission semantics.
- Claiming domain-level network filtering from metadata alone; a higher-assurance container requires an enforceable egress boundary.
- Removing historical dispatch JSON during the compatibility period.

## Decisions

### 1. Integrate through one runtime coordinator

Add a coordinator boundary used by `dispatch_one()` and `dev-agent.py`. It resolves flags once, creates run/PRD/iteration IDs, opens the journal and artifact store, registers SDK hooks, chooses the sandbox, and emits lifecycle events. Standalone modules remain pure and testable; production code must not call them as disconnected helpers.

Alternative rejected: enabling each flag independently at scattered call sites. That permits impossible combinations such as session retry without session persistence or hooks without a journal.

### 2. Use journal-driven decisions only after parity

Shadow mode writes the same event stream while legacy decisions remain authoritative. A parity command compares every real dispatch terminal state, including `stalled`, `orphan_deleted`, `planned`, semantic revise, and external block. Journal-driven mode becomes authoritative only after parity passes for representative fixtures and a real dry-run; reducer failure blocks new automatic side effects, with legacy read fallback limited to one release cycle.

Alternative rejected: the current synthetic cutover helper alone, because it constructs journal chains from expected records and cannot detect integration drift.

### 3. Separate evidence gates from terminal publication

The publication decision requires both independent fresh green TestEvidence and `verify_verdict == pass`. Journal terminal events must carry the mechanical test result and semantic verdict separately. Artifact persistence failure, missing evidence, or unknown reconciliation produces a blocked state, never a green substitute.

### 4. Make iteration input immutable

At dispatch entry, store the PRD content digest and immutable source reference. Each verify/retry round receives a new iteration ID. Feedback is stored as a sanitized content-addressed artifact and referenced by journal; recovery context is generated from the immutable PRD plus referenced artifacts. Historical PRDs remain readable through compatibility readers.

### 5. Treat sandbox policy as an actual boundary

The container adapter must either invoke an enforceable egress policy (for example a configured network namespace/proxy or equivalent deployment policy) or return `sandbox_blocked`. A Docker label is audit metadata only. Requests observed at the control boundary are useful evidence but are not sufficient to claim network enforcement for arbitrary Bash commands.

### 6. Validate failure paths with real adapters

Use fake adapters for deterministic unit tests, but add subprocess-level SDK hook wiring tests, local fixture repositories, a Docker/Podman canary when available, and a documented skip/block result when unavailable. The quality command must run with the declared Python version and archive its exact output and evidence digests.

## Risks / Trade-offs

- [Journal cutover changes recovery semantics] → shadow parity, feature flags, one-release compatibility fallback, and explicit migration reports.
- [Immutable PRD breaks old feedback consumers] → compatibility reader for historical PRDs and recovery context builder for new iterations.
- [Container egress enforcement differs by host] → capability preflight; unavailable enforcement yields `sandbox_blocked`, never silent local fallback.
- [Hook/artifact failure can block productive work] → classify as blocked with operator-visible reason; do not turn missing evidence into success.
- [Real canaries require credentials or container runtimes] → use no-write fixtures, host-side scoped credentials, and record blocked prerequisites as evidence rather than fabricating pass.

## Migration Plan

1. Fix Ruff and run `quality.sh` under Python 3.12; archive output.
2. Add coordinator wiring with all flags defaulting to legacy behavior and add real event/terminal parity fixtures.
3. Enable journal shadow for one allowlisted project; compare real records to reduced states.
4. Enable hooks, session retry, and local assurance tier for the canary; verify no-test, stale-test, semantic-revise, and compaction paths.
5. Enable container tier only after egress/credential preflight passes; otherwise produce `sandbox_blocked`.
6. Run crash drills after agent completion, test completion, commit, push, and PR creation; verify reconciliation before retry.
7. Enable journal-driven dispatch for the canary, retain legacy fallback for one release cycle, then broaden rollout.

## Open Questions

- Which host-level egress mechanism is approved for container network allowlists?
- Which concrete SDK hook registration API and version will the coordinator pin for production?
- What retention/quota policy applies to journal and content-addressed artifacts?
- Should a failed artifact write block only Stop completion or also terminate the current SDK turn immediately?
