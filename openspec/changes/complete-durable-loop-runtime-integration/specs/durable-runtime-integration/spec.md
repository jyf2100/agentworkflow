## ADDED Requirements

### Requirement: Production runtime coordinator
The control plane SHALL resolve loop flags once per dispatch and use one coordinator to connect journal, artifact store, retry policy, hooks, sandbox, telemetry, and reconciliation at the real `run_daily.py`/`dev-agent.py` boundaries.

#### Scenario: Enabled runtime uses coordinator
- **WHEN** a new dispatch has durable runtime flags enabled
- **THEN** the coordinator creates stable run/PRD/iteration IDs, registers required adapters, and emits lifecycle events before the first external side effect

#### Scenario: Disabled runtime preserves baseline
- **WHEN** all durable runtime flags are disabled
- **THEN** dispatch preserves first-phase decisions and does not silently invoke partial durable features

### Requirement: Journal-driven cutover
The control plane SHALL keep legacy dispatch decisions authoritative in shadow mode and SHALL switch new runs to journal-reduced decisions only after real parity evidence passes.

#### Scenario: Real shadow parity
- **WHEN** a representative fixture and a real dry-run complete in shadow mode
- **THEN** every terminal class, including semantic revise, stalled, orphan deletion, planned, test blocked, and external blocked, matches between legacy records and journal reduction

#### Scenario: Reducer failure during driven mode
- **WHEN** journal validation or reduction fails before an external side effect
- **THEN** the run enters a fail-closed blocked state and does not automatically repeat the side effect

### Requirement: Iteration identity
Each retry or recovery attempt SHALL receive a distinct deterministic iteration ID while preserving a parent run/PRD identity.

#### Scenario: Verify revise creates a new iteration
- **WHEN** semantic verification returns `revise`
- **THEN** the next attempt has a new iteration ID and references the prior iteration and feedback artifact
