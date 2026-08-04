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
The standard executor MUST require structured green test evidence for the current candidate changes before it performs commit, push, or pull-request creation.

#### Scenario: Green test for current changes
- **WHEN** a recognized project test finishes with exit code zero and no candidate file changes occur afterward
- **THEN** the executor may proceed to commit and publication actions

#### Scenario: Test was not run
- **WHEN** the development loop finishes without structured test evidence
- **THEN** the executor performs no commit, push, or pull-request creation and returns a `test_not_run` failure result

#### Scenario: Test failed
- **WHEN** the latest recognized project test exits non-zero
- **THEN** the executor performs no commit, push, or pull-request creation and returns a `test_failed` result containing the test command

#### Scenario: Changes occur after the green test
- **WHEN** candidate files change after the latest green test completed
- **THEN** the prior evidence becomes stale and publication remains blocked until a new green test completes

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

