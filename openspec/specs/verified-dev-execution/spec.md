# verified-dev-execution

## Purpose

Specify the control-plane standard development executor that all admitted code projects are invoked through, so that target repositories no longer need their own dev-agent script. Define the shared slug implementation used for branch creation and idempotency matching, the hard test-evidence gate that must be satisfied before commit, push, or pull-request creation, the truthful JSON result contract the executor reports, and the semantic verify-feedback contract (mechanical line anchors + bundle-scoped coverage) that the post-gate `pa-verify` layer enforces on revise feedback. (`pa-verify`'s full output contract — `verdict` / `feedback_section` / `round` — remains owned by `.claude/agents/pa-verify.md`; this spec anchors only the location-element and bundle-trigger facets.)
## Requirements
### Requirement: Control-plane standard executor
The dispatch system SHALL invoke the single control-plane `dev-agent.py` for every admitted code project with the target worktree as its working directory, and SHALL NOT require a dev-agent script to exist in the target repository.

#### Scenario: Dispatch to a repository without a local executor
- **WHEN** an admitted project has a valid target worktree and no `scripts/dev-agent.py` or `scripts/dev-agent.mjs`
- **THEN** dispatch invokes the control-plane executor in that worktree

#### Scenario: Target repository contains a legacy executor
- **WHEN** an admitted project still contains a legacy dev-agent script
- **THEN** dispatch ignores the legacy script and invokes the control-plane executor

### Requirement: Single branch slug implementation
The orchestrator and standard executor MUST use one shared branch slug implementation for branch creation and idempotency matching.

#### Scenario: Compute idempotency key
- **WHEN** dispatch derives the branch slug for a PRD
- **THEN** the value equals the slug the standard executor will place in the created branch name

### Requirement: Verified test evidence before publication
The standard executor MUST require structured green test evidence for the current candidate changes before it performs any publication action — commit, push, or pull-request creation. Injected acceptance tests are recognized project tests for this gate: a red or stale injected acceptance test blocks publication exactly like a red or stale project test, and edits to injected files count as candidate file changes. Merging the branch into main is NOT an executor publication action: the executor SHALL NOT merge into main; that destructive side effect is owned by the single-flight auto-merge closed loop, which runs its own post-merge verification against integrated main (see the single-flight-auto-merge capability). The green-evidence gate applies to the candidate branch only.

#### Scenario: Green test for current changes
- **WHEN** a recognized project test finishes with exit code zero and no candidate file changes occur afterward
- **THEN** the executor MAY proceed to commit and publication actions (commit, push, pull-request creation)

#### Scenario: Test was not run
- **WHEN** the development loop finishes without structured test evidence
- **THEN** the executor performs no commit, push, or pull-request creation and returns a `test_not_run` failure result

#### Scenario: Test failed
- **WHEN** the latest recognized project test exits non-zero
- **THEN** the executor performs no commit, push, or pull-request creation and returns a `test_failed` result containing the test command

#### Scenario: Changes occur after the green test
- **WHEN** candidate files change after the latest green test completed
- **THEN** the prior evidence becomes stale and publication remains blocked until a new green test completes

#### Scenario: Executor does not merge into main
- **WHEN** the candidate branch has passed verify and would otherwise be ready to merge
- **THEN** the executor does not merge it into main; merge is performed only by the single-flight auto-merge closed loop after its own post-merge verification, never by the executor's publication path

#### Scenario: Red injected acceptance test blocks publication
- **WHEN** an injected acceptance test is among the recognized tests run in the dev worktree and its latest execution exits non-zero
- **THEN** publication is blocked with a `test_failed` result naming the injected test, exactly as for a project-authored test

### Requirement: Truthful execution result
The executor MUST expose test status, test command, evidence freshness, branch, cost, turns, and failure reason in its final JSON result, and MUST NOT claim verification passed when evidence is absent or stale.

#### Scenario: Publication succeeds
- **WHEN** the executor publishes a branch or pull request
- **THEN** its JSON result reports fresh green test evidence and the pull-request text states only verified facts

#### Scenario: Publication is blocked
- **WHEN** the test gate blocks publication
- **THEN** the JSON result identifies the precise gate reason and preserves branch/run-log information needed for reconciliation

### Requirement: Dev-agent test command executability across the SDK dev loop

The control-plane standard executor's SDK dev loop MUST keep the bidirectional control channel required by `can_use_tool` available until the dev loop reaches a result, so that the development agent can request the target repository's native test command (for example `npm test` or `node --test`) inside the worktree at any tool turn. An admitted command MUST start and return its real exit status; a denied command MUST remain unexecuted and return the permission gate's structured denial. Neither path may fail because the streaming input or control channel was closed early. The requirement constrains observable executability and permission truthfulness, not a particular async-generator, iterator, or compatibility-patch implementation.

#### Scenario: Dev agent runs the project test command on a later tool turn

- **WHEN** the dev loop has already completed one or more tool calls and the agent invokes the project's native test command (for example `npm test`) inside the worktree
- **THEN** the command reaches the `can_use_tool` permission gate and, when admitted, starts and returns its real exit status — it MUST NOT fail because the streaming input or control channel was closed early

#### Scenario: Permission denial remains truthful

- **WHEN** a later tool-turn test command reaches `can_use_tool` and the permission policy denies it
- **THEN** the command does not execute and the dev loop receives the structured denial reason — it MUST NOT receive `AbortError: Stream closed` in place of the policy decision

#### Scenario: Bidirectional permission channel remains available

- **WHEN** the finite streaming input has delivered its initial user message and the SDK later issues a `can_use_tool` control request before the dev loop result
- **THEN** the executor can write the permission response on the still-available control channel, regardless of whether the input iterable has already yielded its only user message

#### Scenario: Regression locks the executability fix

- **WHEN** the executor's test suite runs with a compatibility patch applied at module load that performs a minimal AST mutation of the pinned SDK's installed `wait_for_result_and_end_input` method body — appending `self.can_use_tool` to the keep-alive condition's `if.test` BoolOp and recompiling into the SDK module namespace, so that the keep-alive covers `can_use_tool` with zero drift from the original method body and byte-level preservation of unrelated logic (for example the 0.2.127 background-task fix #1103)
- **THEN** the deterministic pinned-SDK integration test that previously locked the upstream defect as indefinite `xfail(strict)` MUST now pass strict with the `xfail` marker removed, backed by a structural assertion that the patched source contains exactly one `or self.can_use_tool` occurrence and an identity assertion that the SDK method reference is the patched reference; the patch MUST refuse to apply (raise) when `inspect.getsource` fails, when the keep-alive `BoolOp` is not the precise old form (`self.sdk_mcp_servers or self.hooks`), or when anchors are missing, and MUST skip cleanly when the SDK already covers `can_use_tool` upstream. This closes the gap that previously deferred the real executability verification to natural dispatch verification, which itself stayed RED across the 2026-07-27 dispatches.

#### Scenario: Canary release gate locks real executability

- **WHEN** a change removes the `xfail` marker, alters the compatibility patch's detection logic, or upgrades the SDK pin
- **THEN** that change MUST carry one green real-dispatch canary run as a release gate — a CI `workflow_dispatch` job that re-dispatches an admitted target repo's dev loop and asserts the dev agent runs the project's native test command inside the worktree and returns its real exit status with no `AbortError: Stream closed`; this canary is a required check before cutover and MUST NOT be deferred to natural dispatch verification

### Requirement: Line-anchored verify feedback

When `pa-verify` returns `verdict=revise`, the location element of its `feedback_section` MUST be grounded in mechanically-derived anchors produced by orchestrator logic — the failing test name, the failing file and assertion line parsed from the independent verify output, and the corresponding diff hunk — not locations the model recalls on its own. Anchors are carried in a structured sub-field of `feedback_section`; the model reasons about cause and fix, location is anchored. First-supported runners are `jest` (Node) and `pytest` (Python); any other runner output hits the unresolved path below.

#### Scenario: Red test maps to a diff hunk

- **WHEN** the independent verify run reports a failing test and the orchestrator can parse its failing file and line from the test output (jest or pytest format)
- **THEN** the orchestrator mechanically derives `(test name, failing file:line, matching diff hunk)` and injects them as a structured anchor sub-field in `feedback_section`; the structured anchor field is the machine-asserted contract (the persona cites the injected anchors rather than recalling locations — persona prose compliance is design guidance, not a scenario THEN)

#### Scenario: Anchor cannot be resolved

- **WHEN** the mechanical anchor mapping cannot resolve a failing test to a diff hunk (unparseable test output, unsupported runner, or no hunk matches)
- **THEN** the structured anchor sub-field carries an `unresolved` flag with a reason, and the orchestrator records the gap in the dispatch record (the persona does not fabricate a location — prose compliance is design guidance)

#### Scenario: Anchors recomputed each round

- **WHEN** round ≥ 2 incremental redo produces a new failing test against a new base (the previous dev branch)
- **THEN** the anchors are recomputed against the new diff and fresh test output, never reused from the prior round

#### Scenario: Round ≥ 2 red assertion lands in the base, not the incremental diff

- **WHEN** round ≥ 2 incremental redo changes area A but the failing assertion is on a line that existed in the previous dev branch (base) and is outside the incremental diff
- **THEN** the orchestrator marks the anchor `base-side regression at <file:line>`, distinguished from `unresolved` (which means parse failure), so the revise feedback preserves the regression-location signal instead of degrading to unresolved

### Requirement: Bundle-scoped review coverage for large diffs

When the dev diff exceeds a configured threshold, the orchestrator MUST split it into related-file bundles and feed them to `pa-verify` with isolated per-bundle context. Bundling itself applies to both the green and revise paths; **per-criterion coverage accounting applies only to the revise path**. The green-path contract ("quick-confirm → pass on no major off-target") is unchanged — bundling only splits the glance into per-bundle quick sanity, it does not deepen green-path review into exhaustive accounting.

#### Scenario: Large diff is bundled on both paths

- **WHEN** the dev diff exceeds the configured threshold (file count or total diff lines), regardless of test color
- **THEN** the orchestrator groups related files into bundles (for example an implementation file together with its test and collocated i18n/config) and feeds them to `pa-verify` with isolated per-bundle context — on both the green and the revise path

#### Scenario: Revise-path acceptance-criterion coverage accounting

- **WHEN** `pa-verify` reviews a bundled diff on the revise path (test red)
- **THEN** the orchestrator feeds each bundle with its mapped PRD acceptance criteria, and `pa-verify`'s revise `feedback_section` carries a structured `criteria_coverage` field (per criterion: covered / not-covered-in-bundle) — this structured field is the machine-asserted contract; the persona's prose accounting depth is design guidance

#### Scenario: Green-path quick sanity per bundle

- **WHEN** `pa-verify` reviews a bundled diff on the green path (`test_rc=0`)
- **THEN** the orchestrator feeds bundles **without** per-criterion acceptance-criteria mapping, and `feedback_section` carries no `criteria_coverage` field on the green path (the persona does only per-bundle quick sanity and returns `verdict=pass` on no major off-target — persona quick-confirm behavior is design guidance, not a scenario THEN)

#### Scenario: Small diff stays single-pass

- **WHEN** the dev diff is at or below the configured threshold
- **THEN** single-bundle review is used and no bundle-splitting overhead is incurred


### Requirement: Independent verification execution and scope
When the agent-verification flag is on, independent verification SHALL execute through an agent session (headless claude code) inside a fresh detached worktree, acting as a pure executor: the environment specification, the requirements reference, and the test surface are all injected by the orchestrator — the agent MUST NOT self-source the test command, author or modify test files, or alter source code. The full-gate test command declaration order SHALL be: profile-declared `verify_test_cmd` > dev-reported test-command hint > skip with a recorded note. The pass/fail verdict is mechanically anchored by the orchestrator and is never taken from the agent's self-report:

1. the exit code of the declared test command extracted from the orchestrator-captured raw stream transcript;
2. tracked-file immutability — `git diff` on the verify worktree must be empty after the session;
3. injected-file digest re-check per the acceptance-test-injection capability.

Any anchor violation invalidates the result fail-closed. A session that exceeds its wall-clock limit without completing the declared test command is likewise invalid — timeout is an inconclusive verdict, never red. Node and non-Node projects use this single unified execution path.

#### Scenario: Session wall-clock timeout is invalid, not red

- **WHEN** the verify agent session exceeds its wall-clock limit before the declared test command completes in the transcript
- **THEN** the result is invalid and requires triage — it is never classified as red, matching the mechanical track's timeout semantics (a raised exception, not a failure exit code)

#### Scenario: Green verdict comes from the transcript, not the agent

- **WHEN** the verify agent session completes and the orchestrator extracts exit code zero for the declared test command from its captured raw stream, with both immutability anchors clean
- **THEN** verification records green with the exit-code extraction and anchor checks as evidence — the agent's own summary of the run is not verdict evidence

#### Scenario: Command-form variants preserve exit-code semantics are matchable

- **WHEN** the verify agent executes the declared test command in a chained or prefixed form whose exit code is still semantically the test command's (e.g. `cd <worktree> && npm test`, `rm tmp && npm test` — chain exit code = tail command's; a short-circuited chain yields the failed prefix's non-zero code)
- **THEN** the orchestrator's stream matching uses tail-segment semantics (sharing the same tail-segment and exit-code-semantics primitives as the dev-side evidence collector, while the identity predicate here — executed command ≡ declared command — stays a separate implementation from the dev side's trust predicate) and the tool_result exit code is admitted as verdict evidence
- **BUT WHEN** the executed form corrupts exit-code semantics (pipes, output redirection, command substitution, backgrounding)
- **THEN** the command is not matchable and the result is `unresolved` → invalid — never guessed green

#### Scenario: Invalid verdict has a contract slot, not a bool

- **WHEN** an anchor violation, a session wall-clock timeout, or an unmatchable declared command produces an invalid result on the agent track (or a timeout on the mechanical track)
- **THEN** the verification return contract carries a three-state verdict (`green` / `red` / `invalid`) and every consumer (dispatch record test status, pa-verify input, report stage, triage routing) reads the three-state field — an invalid result is never coerced into `pass=false` and never enters the dev revise loop

#### Scenario: Agent edits source or project tests

- **WHEN** the session ends and `git diff` on the verify worktree is non-empty
- **THEN** the verification result is invalid regardless of any green exit code, the violation is recorded, and the run requires triage

#### Scenario: Profile-declared command wins over dev hint

- **WHEN** the project profile declares `verify_test_cmd` and the dev result also reports a test-command hint
- **THEN** the injected full-gate command is the profile-declared one and the verification note records the source as `profile`

#### Scenario: No declared command and no hint

- **WHEN** neither a profile `verify_test_cmd` nor a dev-reported hint exists
- **THEN** the full-gate portion is skipped with an explicit note, while injected acceptance tests (when present) still run

#### Scenario: Flag off keeps legacy mechanical replay

- **WHEN** the agent-verification flag is off (default)
- **THEN** independent verification keeps the pre-change mechanical replay behavior byte-identically, and no agent session is started

#### Scenario: Complete structured test report is produced on every run

- **WHEN** a verification run completes on either track (mechanical or agent), regardless of outcome — green, red, or invalid
- **THEN** the orchestrator persists a complete structured test report covering run identity (PRD / iteration / branch), the command source and declared commands, per-command exit codes with output pointers (test log paths), the anchor check results (agent track) or install/test return codes (mechanical track), an environment-parity section comparing the declared environment specification against the environment actually assembled as observed in the captured stream (Bash commands, mechanically extracted; recorded, not blocking, in v1), and the verdict with its evidence pointers (test log, captured stream, injected-file digests where applicable) — and the dispatch record references the persisted report path

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
