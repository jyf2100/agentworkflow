## ADDED Requirements

### Requirement: pa-verify three-party review scope

`pa-verify` is positioned as a publish-time independent verification gate that runs **in parallel with** — not replacing — the project's own development-time review. Its `verdict` is composed by the persona over three review dimensions (functional alignment, completeness, quality bar) with the independent test result as a confirming input. **The orchestrator's contract is to supply the inputs and forbid the mechanical mapping, not to assert the persona's reasoning** — the three-dimension composition, the green-but-blocking-defect judgement, and the "skeptical but not harsh" baseline live in the `pa-verify` persona definition (design guidance), not in these scenarios.

The orchestrator SHALL:

1. construct the pa-verify invocation context to carry all three anchor sources together — (a) the independent test result (`test_rc` + test log), (b) the PRD acceptance-criteria list **with stable ids**, (c) the dev diff — so no single source is the sole input;
2. assign each PRD acceptance criterion a **stable id** (derived from the requirement slug, stable across verify revise rounds) and inject the id list into the context — so non-test defects can anchor to a criterion id rather than a free-form description;
3. NOT mechanically map `test_rc → verdict` — neither `verify_prompt` nor the dispatch path SHALL contain a `test_rc == 0 → verdict = pass` hardwire (the persona composes the verdict; a green test alone is not a pass contract — persona composition is design guidance, the absent hardwire is the machine-asserted contract);
4. prune the review-dimension set per the target project's profile (frontend profiles include UX / responsiveness dimensions; pure-backend profiles omit them);
5. align with the Bundle-scoped review coverage requirement — green-path (`test_rc=0`) review carries NO per-criterion coverage accounting (quick sanity only); per-criterion coverage accounting applies only on the revise path.

The `feedback_section` anchor contract splits by defect source, and the two anchor types are **orthogonal**: test-traced defects anchor to the mechanically-derived failing-test location governed by the Line-anchored verify feedback requirement; non-test defects (functional misalignment, dropped acceptance criterion, quality hard-defect) anchor to a PRD acceptance-criterion id or a diff location governed here. This requirement does not modify the Line-anchored requirement's test-traced location anchor.

#### Scenario: pa-verify context carries all three anchor sources

- **WHEN** the orchestrator builds the pa-verify invocation context for a requirement
- **THEN** the context contains the independent test result (`test_rc` + test log), the PRD acceptance-criteria list (with stable ids), and the dev diff together; no single source is the sole input

#### Scenario: No mechanical test_rc-to-verdict mapping

- **WHEN** the orchestrator's `verify_prompt` and dispatch path are inspected
- **THEN** neither contains a `test_rc == 0 → verdict = pass` hardwire; the verdict is composed by the persona over the three dimensions plus the test result (persona composition is design guidance; the absent hardwire is the machine-asserted contract)

#### Scenario: PRD acceptance criteria carry stable ids injected into the context

- **WHEN** the orchestrator prepares a PRD for the verify loop
- **THEN** each acceptance criterion in the PRD carries a stable id (derived from the requirement slug, stable across verify revise rounds), and the pa-verify context receives the id list

#### Scenario: Review dimensions pruned per project profile

- **WHEN** the orchestrator builds the pa-verify prompt for a pure-backend (non-UI) project profile
- **THEN** the dimension set omits UX / design-fidelity dimensions and includes only backend-relevant quality dimensions (security / performance / compatibility); for a frontend profile the dimension set additionally includes UX / responsiveness

#### Scenario: Non-test defect feedback anchors to a criterion id or diff location

- **WHEN** `pa-verify` returns `verdict=revise` for a defect that is NOT a failing test (a correctness bug against a PRD acceptance criterion, a silently-dropped acceptance point, or a quality hard-defect found on a green test)
- **THEN** the `feedback_section` anchor field references a PRD acceptance-criterion id or a diff location — NOT a failing-test location — distinguishing the non-test anchor type from the test-traced anchor governed by the Line-anchored verify feedback requirement

#### Scenario: Green-path review carries no per-criterion coverage accounting

- **WHEN** `pa-verify` reviews a diff on the green path (`test_rc=0`)
- **THEN** the orchestrator does not inject per-criterion coverage accounting into the context (quick sanity only), aligning with the Bundle-scoped review coverage green-path contract; per-criterion coverage accounting applies only on the revise path

### Requirement: Infrastructure-failure triage

The orchestrator SHALL distinguish an infrastructure failure in the independent verify run (dependency install / build step non-zero, for example `npm ci` failing because `package-lock.json` is inconsistent) from a code-level test failure. An infrastructure failure means the system cannot determine whether the code runs correctly — a three-state UNKNOWN condition — and MUST be triaged to a human (terminal `triaged` / `blocked_infra`), not routed into the verify revise loop. Only a code-level test failure (`test_rc` non-zero after a successful install/build) MAY feed `pa-verify` for a `revise` verdict. The orchestrator MUST NOT treat an infrastructure failure as a revise signal that asks the dev agent to fix a code defect it cannot fix.

#### Scenario: Dependency install fails

- **WHEN** the independent verify run's install/build step exits non-zero (`install_rc ≠ 0`) so the test step never runs (`test_rc` is null)
- **THEN** the dispatch record terminates in `triaged` (or `blocked_infra`) with a reason identifying the infrastructure failure, and the verify revise loop is NOT entered; no `revise` feedback is sent to the dev agent

#### Scenario: Install succeeds, test fails

- **WHEN** the install/build step exits zero and the test step exits non-zero (`test_rc ≠ 0`)
- **THEN** the code-level test failure is fed to `pa-verify` which MAY return `verdict=revise`, entering the revise loop normally

#### Scenario: Infrastructure failure is not a revise signal

- **WHEN** an infrastructure failure has been classified
- **THEN** the dev agent is not asked to revise code to fix it, and the failure is surfaced for operator triage (lock state, environment, dependency manifest) rather than consuming a verify round
