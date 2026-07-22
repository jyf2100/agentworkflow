# session-aware-retry

## Purpose

Define how loop iterations persist SDK session identity and retry deterministically by versioned policy. Every iteration records the SDK session ID, result subtype, stop reason, turn count, usage, cost, and compaction count; retry mode (`resume`, `fork`, `new_session`, `stop`) is selected by a versioned policy from structured failure and progress signals rather than free-form model judgment; side effects are reconciled idempotently before any retry; each retry layer (inner Stop-hook continuation, SDK session, outer verify) carries its own limit and obeys total wall-clock and cost budget; and any new or resumed iteration receives a recovery summary derived only from the immutable PRD and journal evidence.

## Requirements

### Requirement: Persist SDK session identity
Every agent iteration SHALL record the SDK session ID, result subtype, stop reason, turn count, usage, cost when available, and compaction count.

#### Scenario: Agent returns an error result
- **WHEN** the SDK emits any error ResultMessage subtype
- **THEN** the session identity and available usage metadata are persisted before retry policy evaluation

### Requirement: Deterministic retry mode selection
Retry mode MUST be selected by a versioned policy from structured failure and progress signals, not by free-form model judgment.

#### Scenario: Transient transport failure with resumable session
- **WHEN** execution stops because of a transient provider or transport error and the session remains recoverable
- **THEN** the policy selects `resume` and records its reason

#### Scenario: Repeated identical failure without progress
- **WHEN** the same normalized failure fingerprint recurs and the diff hash does not change
- **THEN** the policy selects `new_session` or `stop` according to remaining budget rather than repeatedly resuming

#### Scenario: Alternative approach must preserve original history
- **WHEN** a verifier requests an independent alternative approach
- **THEN** the policy selects `fork` and links the new iteration to the source session

### Requirement: Side-effect-safe retry
Before resume, fork, or new-session execution, the system MUST reconcile commit, branch, PR, and other recorded side effects using their idempotency keys.

#### Scenario: Push succeeded before process crash
- **WHEN** the journal lacks completion for a push but the remote branch contains the expected commit
- **THEN** recovery records the push as reconciled and does not push or recreate the branch again

#### Scenario: External state is unknown
- **WHEN** side-effect reconciliation cannot establish remote state
- **THEN** retry is blocked without consuming a retry attempt

### Requirement: Bounded retry hierarchy
Inner Stop-hook continuation, SDK session retry, and outer verify retry SHALL each have independent limits and SHALL also obey the run wall-clock and cost budget.

#### Scenario: Inner continuation limit is reached
- **WHEN** the Stop hook has prevented termination the configured maximum number of times
- **THEN** the SDK session ends and control returns to the outer verifier without silently starting another inner continuation

#### Scenario: Total run budget is exhausted
- **WHEN** any retry layer would exceed total wall-clock, turn, iteration, or trusted cost limits
- **THEN** the run transitions to a terminal budget state and performs no further agent invocation

### Requirement: Recovery context is evidence-derived
New or resumed iterations MUST receive a recovery summary derived from the immutable PRD and journal evidence, including remaining acceptance criteria, changed files, test failures, decisions, and next action.

#### Scenario: New session follows a failed resume
- **WHEN** policy selects a new session after context contamination
- **THEN** the prompt contains the evidence-derived recovery summary but excludes untrusted free-form historical narration that is not referenced by the journal
