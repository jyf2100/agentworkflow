# verified-publication-integrity Specification

## Purpose
TBD - created by archiving change complete-durable-loop-runtime-integration. Update Purpose after archive.
## Requirements
### Requirement: Dual publication gate
The system SHALL emit `published` and permit verified publication only when independent fresh green TestEvidence, semantic `verify_verdict=pass`, and known external reconciliation all hold.

#### Scenario: Tests green but semantic review red
- **WHEN** independent tests pass but `pa-verify` returns `revise`
- **THEN** the journal terminal state is `revise` or `interrupted`, never `published`

#### Scenario: External state unknown
- **WHEN** branch, commit, push, or PR state cannot be determined
- **THEN** retry and publication are blocked without consuming a retry attempt

### Requirement: Evidence integrity
The system SHALL fail closed when required journal or evidence artifacts cannot be persisted, verified, or sanitized.

#### Scenario: Test artifact write fails
- **WHEN** a green test result cannot be stored with a valid artifact digest
- **THEN** Stop/publication cannot treat the result as complete fresh evidence and records an integrity-block reason

#### Scenario: Complete malformed journal tail
- **WHEN** the final journal line is complete but schema-invalid rather than merely truncated
- **THEN** recovery marks the journal corrupt and does not silently discard the line

### Requirement: Immutable new-run input
New runs SHALL preserve the original PRD content and store verifier feedback only as sanitized, content-addressed artifacts referenced by journal events.

#### Scenario: Feedback for a new iteration
- **WHEN** semantic verification returns actionable feedback
- **THEN** the original PRD remains byte-for-byte unchanged and the next recovery context references the feedback artifact

### Requirement: Pull-request lifecycle follows the requirement

The pull request for a given requirement (PRD / slug) SHALL be created at most once across the verify revise loop. The branch and worktree identifiers SHALL be derived from the requirement slug (and a stable PRD id), not from a per-invocation timestamp, so that every verify revise round of the same requirement accumulates commits onto the same branch and the same pull request. The executor SHALL open a pull request idempotently: before creating a PR it MUST check whether an open PR already exists for that branch, and if so MUST only push new commits (which the host merges into the existing open PR automatically) rather than creating a second PR. A verify revise round MUST NOT produce a new pull request for a requirement that already has an open pull request.

#### Scenario: Verify revise round reuses the same branch

- **WHEN** a requirement enters its second (or later) verify revise round
- **THEN** the dev agent operates on the same branch name as the first round (branch derived from the slug, not from a fresh per-invocation timestamp), so the round appends commits to the existing branch rather than creating a new one

#### Scenario: Idempotent pull-request creation

- **WHEN** the executor reaches PR creation and an open pull request already exists for the requirement's branch
- **THEN** the executor pushes new commits to that branch and does not call PR creation; the existing open pull request is updated with the new commits by the host

#### Scenario: No second pull request for one requirement

- **WHEN** a requirement already has an open pull request and a subsequent verify revise round produces new commits
- **THEN** no second pull request is created for that requirement; the in-flight pull-request count for the requirement remains one

#### Scenario: First round creates the single pull request

- **WHEN** a requirement's first verify round produces publishable commits and no open pull request exists for its branch
- **THEN** the executor creates exactly one pull request, which all subsequent revise rounds of that requirement update rather than replace

#### Scenario: Worktree identifier follows the requirement slug

- **WHEN** the executor creates or reclaims the dev worktree for a requirement across verify revise rounds and across cron cycles
- **THEN** the worktree identifier is derived from the requirement slug (not from a per-invocation timestamp), so the same requirement resolves to the same worktree path deterministically; the executor reclaims an existing same-slug worktree rather than leaving a stale timestamped one behind
