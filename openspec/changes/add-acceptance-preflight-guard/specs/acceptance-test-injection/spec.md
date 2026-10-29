## MODIFIED Requirements

### Requirement: Acceptance tests are externally authored control-plane artifacts

The pipeline SHALL derive executable acceptance tests from the PRD's acceptance criteria (stable ids) via a dedicated authoring persona after the critic gate passes, and SHALL NOT let the dev agent or the verify agent author, select, or modify the acceptance test suite. Tests are written in the harness declared by the project profile and stored content-addressed in the artifact store; the orchestrator journals an acceptance-tests event carrying digest and path. The suite's recorded criteria ids MUST cover every acceptance criterion id of the PRD — coverage closes in both directions, and an uncovered criterion is never silently dropped. The orchestrator SHALL self-check the suite on two layers before it can steer the dev loop: a static check at authoring time (structure and import surface) and a collection-level check at dispatch time where the harness environment permits — a suite that cannot run is withheld before it reaches the dev loop, never dispatched as a known-broken oracle.

#### Scenario: Authored from PRD acceptance criteria after critic pass

- **WHEN** a PRD passes the critic gate with n ≥ 1 acceptance criteria carrying stable ids
- **THEN** the authoring persona produces executable test files in the profile-declared harness, each test traceable to at least one criterion id, and the orchestrator stores them content-addressed with the digest journaled

#### Scenario: Every criterion is covered or explicitly uncovered

- **WHEN** the authoring persona returns a suite and the orchestrator checks the suite's recorded criteria ids against the PRD's acceptance criteria ids
- **THEN** every criterion id is covered by at least one test, or the uncovered ids are recorded explicitly in the journal event — an orphan criterion never passes the green gate silently

#### Scenario: Suite is statically self-checked at authoring time

- **WHEN** the authored suite returns, the orchestrator statically checks file shapes, criterion annotations, and the import surface against the harness whitelist — pytest: standard library plus relative imports; jest: relative paths plus the jest global face; playwright: `@playwright/test` plus relative paths — with `playwright.config.ts` exempt from the import whitelist (it is an environment-wiring artifact, not oracle body: it legitimately imports Node builtins such as `path`)
- **THEN** a violation of shape, annotation closure, or the import whitelist withholds the suite before it is stored for injection; the absence and reason (with the the violating import recorded as detail) are journaled — fail-open, the same face as authoring failure — so an import the target repository cannot possibly satisfy (the invented-third-party-dependency family) is intercepted at first occurrence, before any dev turn is spent

#### Scenario: Dispatch-time collection check withholds an uncollectable suite

- **WHEN** dispatch has probe-injected a pytest suite into the prepared worktree (a temporary copy written solely for the collection probe and removed unconditionally afterwards — the dev agent's injection remains the single authoritative injection) and runs the collection-level check (`pytest --collect-only` scoped to the injected files) before the dev loop starts
- **THEN** a collection failure whose signature anchors to the injected suite files causes the orchestrator to start the dev loop without the suite injected (the dispatch-time withholding and its reason are journaled; the stored suite and its version chain are untouched, and the next dispatch retries idempotently); a collection failure anchored outside the suite (worktree conftest, missing harness runtime, timeout) is journaled as indeterminate and does NOT withhold the suite — an environment unknown is never treated as a suite verdict; jest and playwright are exempt from this layer until the orchestrator can prepare the harness environment inside the worktree

#### Scenario: Defective oracle is re-authored, not blamed on code

- **WHEN** a verification run fails with a suite-level collection-failure signature attributable to the suite rather than the implementation, and triage confirms it as `acceptance_suite_defective`
- **THEN** the suite is re-authored as a new content-addressed version (the journal records the version chain), verification re-runs against the new digest, and the dev revision budget is not consumed — an oracle defect is never classified as code red

#### Scenario: No fabricatable test surface

- **WHEN** the PRD has no acceptance criteria, or the profile declares no harness
- **THEN** no acceptance tests are authored and the pipeline records the absence explicitly (no empty or placeholder test suite is invented)

#### Scenario: Dev and verify agents never author acceptance tests

- **WHEN** the dev loop or the verify agent session runs
- **THEN** neither agent can introduce, remove, or rewrite acceptance test files — the injected set is fixed by the orchestrator at injection time
