## ADDED Requirements

### Requirement: Terminal-outcome lesson candidate extraction
The control plane SHALL generate lesson candidates only after a PRD has a recorded terminal outcome and independent verification evidence is available. Candidate generation MUST consume a curated, read-only evidence bundle and MUST NOT mutate the target repository, the immutable PRD, the primary development session metadata, or the terminal outcome.

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
Each lesson candidate MUST conform to a versioned schema containing project ID, PRD ID, iteration references, pattern, corrective action, applicability boundary, non-applicability boundary, evidence references, source outcome, and confidence. The control plane MUST reject candidates that lack readable integrity-checked evidence, are task-specific summaries without a reusable trigger, or do not prescribe an executable corrective action.

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
The control plane MUST keep a valid candidate unpromoted until an equivalent normalized pattern is supported by evidence from at least two distinct PRD IDs in the same project. A single occurrence MAY bypass the recurrence threshold only when an independent verifier confirms a critical invariant violation from a mechanically allowlisted invariant class. Semantic model output alone MUST NOT select or satisfy that exception.

#### Scenario: Same pattern occurs in two PRDs
- **WHEN** equivalent candidates with valid evidence originate from two distinct PRD IDs in one project
- **THEN** the projection promotes or merges them into one active project lesson containing both evidence lineages

#### Scenario: Pattern repeats within one PRD
- **WHEN** the same candidate appears in multiple iterations of one PRD but no other PRD supports it
- **THEN** it remains a candidate and is not promoted by recurrence

#### Scenario: Model labels a unique issue critical
- **WHEN** a candidate has only one source PRD and the model calls it critical but no allowlisted invariant and independent verifier evidence match
- **THEN** the candidate remains unpromoted

#### Scenario: Verified publication invariant is violated once
- **WHEN** independent verification confirms a mechanically allowlisted critical invariant violation for one PRD
- **THEN** the policy may immediately promote the bounded corrective lesson with that verifier evidence

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
- **WHEN** a PRD ends stalled, gate-blocked, verifier-revise-exhausted, or failed with valid evidence
- **THEN** it may contribute candidates under the same schema and promotion rules without being represented as a successful run
