# isolated-observable-execution

## Purpose

Define the execution assurance tiers and observability model for development-agent runs. Each run declares and journals its execution assurance tier (`local-worktree` or `isolated-container`); isolated runs execute as a non-root identity with bounded resources and an explicit network policy; long-lived credentials are never copied into the target worktree; OpenTelemetry-compatible traces correlate PRD run, iteration, SDK session, tool, test, verifier, reconciliation, and publication; telemetry uses an allowlist that excludes prompt bodies, source, full tool output, and secrets by default; and telemetry backend failure degrades observability without converting verified work into failure.

## Requirements

### Requirement: Explicit execution assurance tier
Every development-agent run SHALL declare and journal its execution assurance tier, at minimum `local-worktree` or `isolated-container`.

#### Scenario: Production autonomous run
- **WHEN** a profile requires isolated execution
- **THEN** dispatch starts the configured container sandbox and refuses to fall back to local execution if sandbox startup fails

#### Scenario: Local smoke run
- **WHEN** an operator explicitly selects local-worktree mode
- **THEN** the journal and report identify the lower assurance tier

### Requirement: Sandbox resource and identity isolation
An isolated-container run MUST execute as a non-root identity with only the target worktree writable, PRD/source mounted read-only, a temporary home, bounded CPU/memory/processes, and an explicit network policy.

#### Scenario: Agent reads host credentials
- **WHEN** a tool attempts to access host credential directories or undeclared mounts
- **THEN** the sandbox denies access independently of SDK permission decisions

#### Scenario: Agent contacts undeclared host
- **WHEN** a process attempts network access outside the profile allowlist
- **THEN** the sandbox blocks the connection and records a policy event

### Requirement: Minimal credential exposure
Long-lived GitHub, SMTP, cloud, and model credentials MUST NOT be copied into the target worktree or evidence artifacts; sandbox credentials SHALL be task-scoped and limited to required services.

#### Scenario: Publication follows verified execution
- **WHEN** the run is ready to push and create a PR
- **THEN** the control plane performs publication with host-side credentials after reconciling evidence, unless a separately approved short-lived sandbox credential policy is configured

### Requirement: End-to-end trace correlation
The system SHALL create traceable causal links from PRD run through iteration, SDK session, tool, test, verifier, reconciliation, and publication using stable IDs and OpenTelemetry-compatible context.

#### Scenario: Iteration resumes on another process
- **WHEN** a persisted run is resumed by a new process
- **THEN** new spans link to the prior iteration and session while preserving the same run and PRD correlation IDs

### Requirement: Safe telemetry data model
Telemetry MUST use an allowlist of metadata fields and MUST NOT export prompt bodies, source code, full tool output, authorization data, cookies, environment values, or detected secrets by default.

#### Scenario: Tool output contains a token
- **WHEN** evidence sanitization detects a credential-like value
- **THEN** the exported event contains a redaction marker and artifact reference only

### Requirement: Observable degradation
Telemetry backend failure SHALL NOT convert verified work into failure, but it MUST produce a local degradation event and metric that is visible in the run report.

#### Scenario: OTLP endpoint is unavailable
- **WHEN** spans cannot be exported within the configured timeout
- **THEN** execution continues with local journal evidence and the report marks observability as degraded
