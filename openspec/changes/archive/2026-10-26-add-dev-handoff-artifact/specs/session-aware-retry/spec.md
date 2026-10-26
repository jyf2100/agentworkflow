## MODIFIED Requirements

### Requirement: Recovery context is evidence-derived
New or resumed iterations MUST receive a recovery summary derived from the immutable PRD and journal evidence, including remaining acceptance criteria, changed files, test failures, decisions, and next action. When the journal references a dev handoff artifact for the preceding iteration of the same PRD, the recovery summary MUST incorporate it, and the `decisions` and `next action` fields MUST be taken from the handoff's own account in preference to mechanical derivation. Free-form narration from a dev handoff SHALL enter the recovery prompt only through a journal-referenced, digest-verified artifact.

#### Scenario: New session follows a failed resume
- **WHEN** policy selects a new session after context contamination
- **THEN** the prompt contains the evidence-derived recovery summary but excludes untrusted free-form historical narration that is not referenced by the journal

#### Scenario: Handoff supplies decisions and next action
- **WHEN** the journal references a digest-verified handoff artifact for the preceding iteration
- **THEN** the recovery summary's decisions and next-action sections are populated from the handoff, and the handoff is rendered into the retry prompt as its own labeled block, separate from verifier feedback and prior-PRD lessons

#### Scenario: No handoff falls back to mechanical derivation
- **WHEN** no handoff artifact exists for the preceding iteration
- **THEN** the recovery summary is derived exactly as before this change (PRD plus journal evidence), with no placeholder handoff content

#### Scenario: Handoff content never gates verdicts
- **WHEN** a handoff is incorporated into a recovery or retry prompt
- **THEN** it acts as context for the next dev iteration only; no verdict, gate, or retry-policy decision is derived from handoff content
