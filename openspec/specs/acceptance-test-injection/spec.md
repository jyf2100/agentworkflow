# acceptance-test-injection

## Purpose

The acceptance-test-injection capability: acceptance tests are externally authored control-plane artifacts (authored from the PRD acceptance criteria by a dedicated persona after the critic gate), dual-injected content-addressed into the dev loop and the verify worktree, and mechanically integrity-checked at consumption time. dev and verify agents never author or alter the oracle.
## Requirements

### Requirement: Acceptance tests are externally authored control-plane artifacts

The pipeline SHALL derive executable acceptance tests from the PRD's acceptance criteria (stable ids) via a dedicated authoring persona after the critic gate passes, and SHALL NOT let the dev agent or the verify agent author, select, or modify the acceptance test suite. Tests are written in the harness declared by the project profile and stored content-addressed in the artifact store; the orchestrator journals an acceptance-tests event carrying digest and path. The suite's recorded criteria ids MUST cover every acceptance criterion id of the PRD — coverage closes in both directions, and an uncovered criterion is never silently dropped.

#### Scenario: Authored from PRD acceptance criteria after critic pass

- **WHEN** a PRD passes the critic gate with n ≥ 1 acceptance criteria carrying stable ids
- **THEN** the authoring persona produces executable test files in the profile-declared harness, each test traceable to at least one criterion id, and the orchestrator stores them content-addressed with the digest journaled

#### Scenario: Every criterion is covered or explicitly uncovered

- **WHEN** the authoring persona returns a suite and the orchestrator checks the suite's recorded criteria ids against the PRD's acceptance criteria ids
- **THEN** every criterion id is covered by at least one test, or the uncovered ids are recorded explicitly in the journal event — an orphan criterion never passes the green gate silently

#### Scenario: Suite is self-checked at authoring time

- **WHEN** the authored suite returns and the declared harness supports collection-only validation (the pytest `--collect-only` class)
- **THEN** the orchestrator runs that validation before injection; a suite-level failure (syntax or collection error) withholds the suite — the absence and reason are recorded (fail-open, same face as authoring failure) instead of dispatching a known-broken oracle

#### Scenario: Defective oracle is re-authored, not blamed on code

- **WHEN** a verification run fails with a suite-level collection-failure signature attributable to the suite rather than the implementation, and triage confirms it as `acceptance_suite_defective`
- **THEN** the suite is re-authored as a new content-addressed version (the journal records the version chain), verification re-runs against the new digest, and the dev revision budget is not consumed — an oracle defect is never classified as code red

#### Scenario: No fabricatable test surface

- **WHEN** the PRD has no acceptance criteria, or the profile declares no harness
- **THEN** no acceptance tests are authored and the pipeline records the absence explicitly (no empty or placeholder test suite is invented)

#### Scenario: Dev and verify agents never author acceptance tests

- **WHEN** the dev loop or the verify agent session runs
- **THEN** neither agent can introduce, remove, or rewrite acceptance test files — the injected set is fixed by the orchestrator at injection time

### Requirement: Dual injection of the acceptance suite

The orchestrator SHALL inject the same content-addressed acceptance suite into both consumption points: the dev loop (as the implementation target, via an acceptance-tests argument carrying path and digest) and the verify worktree (as the independent gate, via mechanical copy performed by the orchestrator itself, never by an agent).

#### Scenario: Dev loop receives the suite as implementation target

- **WHEN** dispatch starts a dev loop for a PRD with an authored acceptance suite
- **THEN** the dev-agent invocation carries the injected suite path and digest, and the dev agent treats making those tests green as part of its goal

#### Scenario: Verify worktree receives the suite mechanically

- **WHEN** independent verification builds its fresh worktree
- **THEN** the orchestrator (not any agent) copies the stored suite into the worktree and records the exact injected file set before the verify agent session starts

#### Scenario: Both injection points consume the same digest within one dispatch iteration

- **WHEN** a dispatch iteration injects the suite into the dev loop and later into the verify worktree
- **THEN** both injections carry the same content-addressed digest — a re-authored (superseded) digest version takes effect only on the next dispatch, and an oracle-defect re-run of verification reuses the digest version the dev implementation was built against, so a rewritten oracle never introduces an untested new examination surface mid-iteration

### Requirement: Injection integrity is mechanically checked

Every consumer of the injected suite MUST verify its digest against the journaled artifact digest at consumption time. In the verify worktree, after the verify agent session ends, the orchestrator SHALL re-hash the injected files; any mismatch (agent-edited test) invalidates the verification result fail-closed. In the dev worktree, edits to injected files count as candidate file changes for the evidence-freshness gate.

#### Scenario: Tampered injected test in verify invalidates the verdict

- **WHEN** the verify agent session ends and a re-hash of any injected file differs from the injected digest
- **THEN** the verification result is marked invalid (not red, not green) with the mismatch recorded, and the run requires triage

#### Scenario: Dev edit of an injected test is a candidate change

- **WHEN** the dev agent modifies an injected acceptance test file in its worktree
- **THEN** the edit is tracked like any candidate file change — it does not silently loosen the acceptance contract, and green evidence covering the edit is required before publication
