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

