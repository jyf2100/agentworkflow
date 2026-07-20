# fail-safe-dispatch

## Purpose

Define how project-pipeline dispatch behaves when the source of truth (GitHub or Git remote) cannot be reliably queried. Replace implicit "treat unknown as not-found" semantics with explicit three-state outcomes so that dispatch admission, idempotency, in-flight limits, and reconciliation fail safe — blocking work and preserving remote state rather than over-asserting or destructively reconciling.

## Requirements

### Requirement: Explicit external lookup outcome
Every dispatch-critical GitHub or Git remote lookup MUST return an explicit `FOUND`, `NOT_FOUND`, or `UNKNOWN` outcome with diagnostic context.

#### Scenario: Valid empty query response
- **WHEN** the external command succeeds and returns a valid empty result
- **THEN** the lookup returns `NOT_FOUND`

#### Scenario: External command cannot establish state
- **WHEN** the command times out, exits non-zero, lacks authentication, is unavailable, or returns invalid data
- **THEN** the lookup returns `UNKNOWN` and records a sanitized reason

### Requirement: Fail-safe dispatch admission
Dispatch SHALL start a development agent only when branch protection, idempotency state, and in-flight PR count are all known and satisfy their admission rules.

#### Scenario: Idempotency state is unknown
- **WHEN** existing pull requests and remote branches cannot be queried reliably
- **THEN** dispatch records `blocked_external_state` and does not start the development agent

#### Scenario: In-flight count is unknown
- **WHEN** the open pull-request count cannot be established
- **THEN** dispatch records `blocked_external_state` instead of treating the count as zero

#### Scenario: All admission checks are known and pass
- **WHEN** protection is enabled, no matching dispatch exists, and the known in-flight count is below the configured limit
- **THEN** dispatch may create the worktree and invoke the development agent

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
