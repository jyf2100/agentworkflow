# durable-loop-state

## Purpose

Define the durable runtime state model for project-pipeline loops. Every loop transition is persisted as an append-only, versioned journal event before it can trigger any external side effect; an explicit state machine separates SDK success from verified or published work; large evidence is stored as content-addressed artifacts with integrity checks; journal recovery tolerates incomplete trailing records but fails closed on corruption inside committed history; and the original PRD remains the immutable requirement source, with verify feedback and execution progress recorded in the iteration journal rather than appended to the PRD.

## Requirements

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

### Requirement: Run directory naming contract
The run directory naming format `<stamp>_<slug>` under `state/runs/<project>/` SHALL be part of the journal contract, not an incidental implementation detail: external indexers (the execution hub DB and its on-volume `.meta.json` signing) rely on this format to align runs with their audit metadata. `<stamp>` SHALL be an 8-digit date (`YYYYMMDD`) with an optional collision-avoidance suffix (`m`, `m2`, `m3`, ... — the `_bump_stamp_suffix` form). Changing the format MUST go through change review — it is no longer a free-form internal convention.

#### Scenario: Stamp collision bump is conditional
- **WHEN** the inject stage runs with a `--stamp` for which no `prd_manifest_<stamp>.json` exists in the state directory (the global stamp namespace, across projects; under the execution-hub deployment this namespace becomes per-project volume-local at host `<data_root>/<project>/state` — the in-container pa perspective and this conditional-bump contract are unchanged, ontological audit B3)
- **THEN** the stamp is used as passed (no bump); a bump suffix is appended ONLY while `prd_manifest_<stamp>.json` exists — the execution hub relies on this conditionality when it pre-allocates a fresh stamp (scanning existing manifests) and passes it via `--stamp`, so that meta file, `--stamp`, and actual run directory stay aligned

#### Scenario: Hub aligns meta signing to run directory
- **WHEN** the execution hub writes its signature meta file before starting a container
- **THEN** it derives `<stamp>_<slug>` from the PRD artifact naming and places `<stamp>_<slug>.meta.json` in the same run directory shape, expecting pa to keep the format stable

#### Scenario: Format change requires review
- **WHEN** a future change proposes renaming or restructuring run directories (e.g. adding fields, changing the stamp format)
- **THEN** the change must explicitly address the impact on external indexers (hub DB, meta files, recovery tooling) rather than landing as an internal refactor

### Requirement: Dev handoff artifact
The dev loop SHALL produce a handoff — the dev's own structured account of the same PRD's preceding iteration (completed / in progress / blockers / approaches tried without success / suggested next step / key decision rationale) — at every finish exit (normal publish, stalled, off-track, publication-gate block, error) and at every assistant-turn checkpoint (each updated handoff block emitted by the dev is absorbed as it appears; no separate turn timer), and the orchestrator SHALL store it as an immutable content-addressed artifact whose digest, size, type, and path are referenced by a journal event. A handoff artifact MUST NOT be modified after recording, and a later iteration MUST produce a new artifact rather than overwrite an earlier one. A handoff MUST NOT be appended to the PRD.

#### Scenario: Every JSON-emitting finish exit carries a handoff
- **WHEN** the dev loop ends at a finish exit that emits a dev result JSON (publish, stalled exit 12, off-track exit 15, gate block exit 14)
- **THEN** the JSON carries a `handoff` field (null when the dev never produced one — honest absence, never fabricated), and the orchestrator stores it as a content-addressed artifact referenced by the iteration's journal events

#### Scenario: Exits without a JSON emit are covered by snapshot adoption
- **WHEN** the dev loop ends without emitting a dev result JSON (error exit 11, or a hard kill)
- **THEN** the most recent handoff snapshot, when one exists, is adopted by the orchestrator as the iteration's handoff artifact, with the journal event marked as snapshot-adopted

#### Scenario: Stored handoffs survive orchestrator death
- **WHEN** the orchestrator process (or its container) dies after a handoff artifact was stored and its journal event emitted
- **THEN** the journal event and the content-addressed artifact survive on the state volume and remain digest-verifiable for a later process to reference; a snapshot not yet adopted at death is honestly absent in ephemeral-container forms (absence is a normal state, never fabricated)

#### Scenario: Recorded handoff digest mismatch fails closed
- **WHEN** a stored handoff artifact no longer matches its recorded digest
- **THEN** injection of that handoff is refused with an evidence-integrity error, consistent with the content-addressed evidence invariant (the run is not silently continued with a modified handoff)

#### Scenario: Absent handoff is not fabricated
- **WHEN** no handoff was ever produced for the preceding iteration (baseline flow, or a kill before the first checkpoint)
- **THEN** no handoff artifact is referenced and downstream consumers fall back to their existing behavior; absence is a normal state, not an error

#### Scenario: Baseline behavior is unchanged without the flag
- **WHEN** dev-agent runs in baseline mode without `--expect-handoff` (write-side enablement) and without `--handoff-artifact` (injection), and no handoff was stored
- **THEN** prompts and recovery summaries are byte-identical to the pre-change behavior
