# loop-lifecycle-controls

## Purpose

Define the in-session lifecycle controls that govern a single development-agent iteration. Every state-changing tool call passes a deterministic PreToolUse policy; PostToolUse processing pairs each result with its tool-use ID and records structured evidence; the Stop hook gates completion on fresh green test evidence under a bounded continuation limit; a recovery snapshot is persisted before any context compaction; and subagent start/stop events are attributed to the parent iteration without granting subagents publication authority.

## Requirements

### Requirement: Pre-tool deterministic policy
Every state-changing tool call MUST pass a deterministic PreToolUse policy for tool availability, repository path, command, network destination, and protected resources before execution.

#### Scenario: Command violates policy
- **WHEN** the agent requests a command outside the allowed repository, command family, or network policy
- **THEN** the hook denies execution, returns a sanitized reason to the agent, and journals the denial

### Requirement: Post-tool structured evidence
PostToolUse processing SHALL associate each tool result with its tool-use ID and record structured exit status, changed-path summary, test evidence, and sanitized output reference.

#### Scenario: Project test completes
- **WHEN** a recognized test tool or command returns
- **THEN** the hook records the exact command, exit status, candidate-content binding, and output artifact reference

#### Scenario: Tool result cannot be paired
- **WHEN** a result lacks a known tool-use ID
- **THEN** the evidence is marked unpaired and cannot satisfy a publication or verification gate

### Requirement: Stop hook completion gate
The Stop hook MUST prevent normal agent completion when the first-phase fresh-green-test requirement is not satisfied, subject to a bounded continuation limit.

#### Scenario: Agent stops without testing
- **WHEN** Claude produces a final response but no fresh green test evidence exists
- **THEN** the Stop hook blocks completion and returns an actionable test-gate reason

#### Scenario: Fresh green evidence exists
- **WHEN** Claude stops and current candidate content matches fresh green test evidence
- **THEN** the Stop hook permits completion while leaving clean-worktree verification to the outer loop

### Requirement: Pre-compaction recovery snapshot
Before automatic or manual context compaction, the system SHALL persist a recovery snapshot containing objective, acceptance criteria, current base/head, changed files, decisions, test evidence, failures, and next actions.

#### Scenario: Compaction occurs
- **WHEN** the SDK fires PreCompact
- **THEN** the snapshot is durably journaled before compaction continues

#### Scenario: Snapshot persistence fails
- **WHEN** the recovery snapshot cannot be persisted
- **THEN** compaction or subsequent automatic retry is blocked rather than discarding unrecoverable context

### Requirement: Subagent lifecycle evidence
Subagent start and stop events MUST record parent iteration, agent identity, delegated objective, tool surface, effort, status, and result artifact without granting subagents publication authority.

#### Scenario: Subagent completes a task
- **WHEN** a subagent returns to the parent
- **THEN** its result is linked to the parent iteration and the parent remains responsible for integration and full verification
