## ADDED Requirements

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
