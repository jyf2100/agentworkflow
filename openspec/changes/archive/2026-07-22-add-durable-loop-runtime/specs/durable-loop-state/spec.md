## ADDED Requirements

### Requirement: Append-only iteration journal
The control plane SHALL persist every loop transition as an append-only, versioned journal event before the transition can trigger its next external side effect.

#### Scenario: Start an iteration
- **WHEN** dispatch is ready to invoke the development agent for a PRD
- **THEN** it durably records the run ID, PRD ID, iteration ID, base, input hashes, and planned action before invocation

#### Scenario: Process crashes after a durable event
- **WHEN** the process restarts after recording an event but before completing the next action
- **THEN** the reducer reconstructs the last valid state and reconciliation determines whether that action already occurred before retrying it

### Requirement: Explicit loop state machine
The system MUST validate every iteration state transition and MUST NOT treat an SDK success result as verified or published work.

#### Scenario: SDK loop completes normally
- **WHEN** the agent emits a successful ResultMessage
- **THEN** the iteration transitions to `agent_finished` and still requires test and verification gates

#### Scenario: Invalid transition is requested
- **WHEN** code attempts to transition directly from `running` to `published`
- **THEN** the state machine rejects the transition, records an error event, and performs no publication action

### Requirement: Content-addressed evidence artifacts
Large evidence SHALL be stored as immutable content-addressed artifacts, and journal events SHALL reference their hash, size, type, path, and sensitivity class.

#### Scenario: Persist test output and diff
- **WHEN** an iteration produces test output or a branch diff
- **THEN** the system stores each artifact, verifies its digest, and records the references in the journal

#### Scenario: Evidence is modified after recording
- **WHEN** an artifact no longer matches its recorded digest
- **THEN** verification and automatic recovery are blocked with an evidence-integrity error

### Requirement: Corruption-aware journal recovery
Journal readers MUST tolerate a single incomplete trailing record but MUST fail closed on malformed or missing records inside committed history.

#### Scenario: Crash leaves a partial final line
- **WHEN** startup finds an incomplete final journal event
- **THEN** it ignores that trailing record, emits a recovery warning, and resumes from the preceding valid boundary

#### Scenario: Middle record is corrupt
- **WHEN** a committed record before later valid events cannot be parsed or validated
- **THEN** the run is marked `state_corrupt` and requires operator recovery

### Requirement: Immutable PRD source
The original PRD SHALL remain the immutable requirement source, and verify feedback or execution progress MUST be recorded in the iteration journal rather than appended to the PRD.

#### Scenario: Verifier requests revision
- **WHEN** pa-verify returns actionable feedback
- **THEN** the system stores the feedback as a journal artifact and builds the next iteration context without modifying the PRD file
