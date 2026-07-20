# verified-dev-execution

## Purpose

Specify the control-plane standard development executor that all admitted code projects are invoked through, so that target repositories no longer need their own dev-agent script. Define the shared slug implementation used for branch creation and idempotency matching, the hard test-evidence gate that must be satisfied before commit, push, or pull-request creation, and the truthful JSON result contract the executor reports.

## Requirements

### Requirement: Control-plane standard executor
The dispatch system SHALL invoke the single control-plane `dev-agent.py` for every admitted code project with the target worktree as its working directory, and SHALL NOT require a dev-agent script to exist in the target repository.

#### Scenario: Dispatch to a repository without a local executor
- **WHEN** an admitted project has a valid target worktree and no `scripts/dev-agent.py` or `scripts/dev-agent.mjs`
- **THEN** dispatch invokes the control-plane executor in that worktree

#### Scenario: Target repository contains a legacy executor
- **WHEN** an admitted project still contains a legacy dev-agent script
- **THEN** dispatch ignores the legacy script and invokes the control-plane executor

### Requirement: Single branch slug implementation
The orchestrator and standard executor MUST use one shared branch slug implementation for branch creation and idempotency matching.

#### Scenario: Compute idempotency key
- **WHEN** dispatch derives the branch slug for a PRD
- **THEN** the value equals the slug the standard executor will place in the created branch name

### Requirement: Verified test evidence before publication
The standard executor MUST require structured green test evidence for the current candidate changes before it performs commit, push, or pull-request creation.

#### Scenario: Green test for current changes
- **WHEN** a recognized project test finishes with exit code zero and no candidate file changes occur afterward
- **THEN** the executor may proceed to commit and publication actions

#### Scenario: Test was not run
- **WHEN** the development loop finishes without structured test evidence
- **THEN** the executor performs no commit, push, or pull-request creation and returns a `test_not_run` failure result

#### Scenario: Test failed
- **WHEN** the latest recognized project test exits non-zero
- **THEN** the executor performs no commit, push, or pull-request creation and returns a `test_failed` result containing the test command

#### Scenario: Changes occur after the green test
- **WHEN** candidate files change after the latest green test completed
- **THEN** the prior evidence becomes stale and publication remains blocked until a new green test completes

### Requirement: Truthful execution result
The executor MUST expose test status, test command, evidence freshness, branch, cost, turns, and failure reason in its final JSON result, and MUST NOT claim verification passed when evidence is absent or stale.

#### Scenario: Publication succeeds
- **WHEN** the executor publishes a branch or pull request
- **THEN** its JSON result reports fresh green test evidence and the pull-request text states only verified facts

#### Scenario: Publication is blocked
- **WHEN** the test gate blocks publication
- **THEN** the JSON result identifies the precise gate reason and preserves branch/run-log information needed for reconciliation
