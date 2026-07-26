# cross-prd-learning-memory

## Purpose

Define an advisory cross-PRD learning memory for the control plane. After a PRD reaches a terminal outcome, the control plane extracts reusable failure-pattern candidates from a curated read-only evidence bundle whose selection is driven by the journal's actual verifier transition history — not by the terminal status label. Candidate facts live in an append-only per-project history with a deterministic rebuildable catalog projection. A candidate is promoted to an active project lesson only after a byte-equal equivalence key recurs across at least two distinct PRDs in the same project. Before a later PRD session starts, at most five relevant active lessons are injected into the development prompt, and every injected lesson receives terminal effectiveness feedback that drives confidence, supersession, and retirement. Learning memory is fail-open by construction: it MUST NOT change test, verification, publication, retry, or terminal predicates, and all memory state lives in ignored control-plane paths — never in target worktrees, commits, pull requests, or immutable PRDs.

## Requirements

### Requirement: Terminal-outcome lesson candidate extraction
The control plane SHALL generate lesson candidates only after a PRD has a recorded terminal outcome and the authoritative evidence matching the journal's actual verifier transition history is available. Evidence selection is driven by that journal history, not by the terminal status label: when the journal records that a verifier event ran (verifier pass, or verifier revise ending in pass or revise-exhaustion), the verifier verdict or revise sequence MUST be referenced, even if a later short-circuit (e.g. publication reconcile UNKNOWN) produced the terminal status. When the journal records no verifier event (a pre-verifier short-circuit such as gate-blocked, stalled, external-blocked, sandbox-blocked, aborted, or state-corrupt), the matching mechanical gate, stall, SDK/session, journal, or external-state evidence MUST be referenced instead of a verifier verdict it never produced. Candidate generation MUST consume a curated, read-only evidence bundle and MUST NOT mutate the target repository, the immutable PRD, the primary development session metadata, or the terminal outcome.

#### Scenario: Verified PRD reaches a terminal outcome
- **WHEN** a PRD finishes with its SDK result, test evidence, verifier verdict, and terminal status recorded
- **THEN** the control plane invokes bounded read-only candidate extraction using references to those facts

#### Scenario: Candidate extraction fails
- **WHEN** the reflection SDK call times out, returns invalid output, or cannot persist its artifact
- **THEN** the original PRD outcome remains unchanged and the control plane records a learning-memory degradation event

#### Scenario: Stop hook fires before terminal outcome
- **WHEN** an SDK Stop hook fires one or more times during the development session
- **THEN** no cross-PRD lesson is generated or promoted from that hook alone

### Requirement: Structured and evidence-grounded candidates
Each lesson candidate MUST conform to a versioned schema containing project ID, PRD ID, iteration references, schema-constrained enum fields (`phase`, `failure_class`, `corrective_action_class`, `applies_when_tags`), a bounded free-text `corrective_action` describing the executable corrective step for audit and prompt injection, a free-text pattern description for audit only, applicability and non-applicability boundaries, evidence references, source outcome, and confidence. The `corrective_action_class` enum participates only in the equivalence key; the `corrective_action` text carries the executable content injected into later PRD prompts under a schema length limit. The schema MUST NOT accept a model-authored `pattern_key` or `equivalence_key`; these are derived mechanically from the enum fields. An `invariant_class` field, if present, is an audit-only label asserted by the verifier and MUST NOT drive promotion. The control plane MUST reject candidates that lack readable integrity-checked evidence, are task-specific summaries without a reusable trigger, do not prescribe an executable corrective action, or carry any enum value outside the controlled vocabulary.

#### Scenario: Candidate describes a reusable failure pattern
- **WHEN** extraction identifies a repeated class of mistake and cites valid test, journal, or verifier evidence
- **THEN** the candidate is appended to the project candidate history with its source PRD identity

#### Scenario: Candidate is only a run summary
- **WHEN** output merely lists files changed or restates the current PRD without a reusable applicability predicate and corrective action
- **THEN** schema validation rejects it from the candidate history

#### Scenario: Candidate evidence is corrupt
- **WHEN** a referenced artifact is missing or fails its recorded digest check
- **THEN** the candidate is rejected and cannot affect the project lesson catalog

### Requirement: Append-only candidates and rebuildable project catalog
The control plane SHALL store candidate facts in an append-only per-project history and SHALL maintain the active lesson catalog as a deterministic rebuildable projection. Both stores MUST live in ignored control-plane state and MUST NOT be written to target repositories or PRD files.

#### Scenario: Catalog is rebuilt
- **WHEN** the active project catalog is missing or deliberately regenerated
- **THEN** the system reconstructs the same lesson states from valid candidate, promotion, usage, outcome, supersession, and retirement facts

#### Scenario: Target worktree is inspected
- **WHEN** a PRD run generates or consumes learning memory
- **THEN** no memory file appears in the target repository worktree or resulting pull request

### Requirement: Cross-PRD promotion policy
The control plane MUST keep a valid candidate unpromoted until candidates with a byte-equal `equivalence_key` — derived deterministically from `(phase, failure_class, corrective_action_class, applicability_signature)` under a project scope — are supported by evidence from at least two distinct PRD IDs in the same project. No single-occurrence exception exists in V1; even verifier-confirmed invariant violations require recurrence across at least two distinct PRDs before promotion. Semantic model output alone MUST NOT select equivalence, promotion count, or storage scope, and an unknown enum value MUST NOT participate in implicit merging.

#### Scenario: Same pattern occurs in two PRDs
- **WHEN** equivalent candidates with valid evidence originate from two distinct PRD IDs in one project
- **THEN** the projection promotes or merges them into one active project lesson containing both evidence lineages

#### Scenario: Pattern repeats within one PRD
- **WHEN** the same candidate appears in multiple iterations of one PRD but no other PRD supports it
- **THEN** it remains a candidate and is not promoted by recurrence

#### Scenario: Verifier-confirmed invariant violation occurs in one PRD
- **WHEN** independent verification confirms a critical invariant violation but only one PRD produced the candidate
- **THEN** the candidate remains unpromoted; no fast path bypasses the cross-PRD recurrence threshold in V1

### Requirement: Bounded relevant lesson retrieval
Before a later PRD development session starts, the control plane SHALL select only active lessons from that project and SHALL rank them using deterministic task metadata and lesson applicability fields. It MUST inject no more than five lessons and MUST include each lesson's trigger, corrective action, and non-applicability boundary without injecting historical transcripts or unrelated evidence bodies.

#### Scenario: Relevant active lessons exist
- **WHEN** a new PRD matches active lesson applicability metadata
- **THEN** the prompt receives the highest-ranked relevant lessons up to the configured limit of five and records their lesson IDs

#### Scenario: Only unrelated lessons exist
- **WHEN** active lessons do not match the new PRD metadata
- **THEN** the development prompt contains no learning-memory block

#### Scenario: Catalog cannot be trusted
- **WHEN** the catalog is malformed or cannot be reconciled with its append-only facts
- **THEN** the system skips lesson injection, records degraded memory, and continues the underlying dispatch without representing that lessons were applied

### Requirement: Lesson effectiveness feedback and lifecycle
For every injected lesson, the control plane SHALL record whether the development run exhibited the prescribed action and whether the associated failure pattern recurred according to test, journal, and verifier evidence. The catalog projection MUST support confirmation, confidence reduction, supersession, and retirement without deleting historical facts.

#### Scenario: Injected lesson prevents recurrence
- **WHEN** a lesson is injected, its corrective action is evidenced, and the associated failure does not recur through terminal verification
- **THEN** the system records a successful application and increases or preserves the lesson's confidence within configured bounds

#### Scenario: Injected lesson is contradicted
- **WHEN** later verifier evidence shows the lesson was inapplicable or its corrective action caused or failed to prevent the associated problem
- **THEN** the system records the contradiction and deterministically downgrades, supersedes, or retires the lesson

#### Scenario: Retired lesson remains auditable
- **WHEN** a lesson is retired
- **THEN** it is excluded from future retrieval while its source and lifecycle facts remain available for reconstruction and audit

### Requirement: Memory isolation and truthful degradation
Learning memory SHALL be advisory and MUST NOT change test, verification, publication, retry, or terminal predicates. Memory failures MUST be observable, while corrupt or unverified memory MUST fail closed for promotion and injection.

#### Scenario: Successful PRD has memory outage
- **WHEN** the PRD passes its existing gates but candidate extraction or catalog persistence fails
- **THEN** the PRD retains its successful outcome and reports learning memory as degraded

#### Scenario: Failed PRD produces useful evidence
- **WHEN** a PRD ends stalled, gate-blocked, verifier-revise-exhausted, or failed with valid evidence of the matching terminal class
- **THEN** it may contribute candidates under the same schema and promotion rules without being represented as a successful run, referencing the mechanical evidence matching its journal history rather than a verifier verdict it never produced

#### Scenario: Post-verifier short-circuit still references verifier evidence
- **WHEN** a PRD's verifier passed but a later publication reconcile returned UNKNOWN, yielding a `blocked_external_state` terminal status despite a recorded verifier event
- **THEN** the candidate references the verifier pass verdict plus the reconcile UNKNOWN record, because the journal's verifier transition history — not the terminal label — decides the evidence class

#### Scenario: Verifier revise exhausted
- **WHEN** a PRD exhausted verifier revise attempts and ended `verifier-revise-exhausted`
- **THEN** the candidate references the verifier revise verdict sequence and exhaustion record, not a mechanical short-circuit evidence class it never produced
