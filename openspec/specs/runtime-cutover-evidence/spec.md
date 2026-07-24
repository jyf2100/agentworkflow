# runtime-cutover-evidence Specification

## Purpose
TBD - created by archiving change complete-durable-loop-runtime-integration. Update Purpose after archive.
## Requirements
### Requirement: Reproducible quality evidence
The rollout SHALL execute the repository quality command with the declared supported Python version and SHALL archive its exact output and evidence digests before marking rollout ready.

#### Scenario: Quality command has lint failures
- **WHEN** compile, tests, or Ruff exits non-zero
- **THEN** rollout readiness is false and the change cannot be marked complete

#### Scenario: Quality command passes
- **WHEN** compile, all tests, and Ruff pass
- **THEN** the result records interpreter version, command, counts, timestamp, and archived evidence digests

### Requirement: Real adapter canaries
The rollout SHALL exercise real SDK hook registration and at least one Node and Python fixture through each configured assurance tier, or record a fail-closed blocked prerequisite when the runtime is unavailable.

#### Scenario: Container runtime unavailable
- **WHEN** the configured container runtime or enforceable egress policy is unavailable
- **THEN** the canary reports `sandbox_blocked` and does not claim a higher-assurance pass

#### Scenario: SDK hook wiring
- **WHEN** a real SDK fixture emits PreToolUse, PostToolUse, Stop, and PreCompact events
- **THEN** the coordinator journals them with correlation IDs and applies the corresponding decisions

### Requirement: Crash and operator recovery evidence
The rollout SHALL run crash drills after agent completion, test completion, commit, push, and PR creation, and SHALL provide executable operator recovery commands for journal corruption and missing sessions.

#### Scenario: Crash after push
- **WHEN** the process crashes after push but before the publish event
- **THEN** restart reconciles the remote branch before any retry and does not push a duplicate side effect

#### Scenario: Documented recovery command
- **WHEN** an operator follows the runbook for a corrupt journal
- **THEN** every referenced command exists in the repository and produces a verifiable recovery or explicit manual-block result

