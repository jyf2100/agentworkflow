## MODIFIED Requirements

### Requirement: Verified test evidence before publication
The standard executor MUST require structured green test evidence for the current candidate changes before it performs any publication action — commit, push, or pull-request creation. Injected acceptance tests are recognized project tests for this gate: a red or stale injected acceptance test blocks publication exactly like a red or stale project test, and edits to injected files count as candidate file changes. Merging the branch into main is NOT an executor publication action: the executor SHALL NOT merge into main; that destructive side effect is owned by the single-flight auto-merge closed loop, which runs its own post-merge verification against integrated main (see the single-flight-auto-merge capability). The green-evidence gate applies to the candidate branch only. When the gate blocks with stale evidence, the executor MAY perform at most one orchestrator-side re-verification by re-running the same recognized test command and collecting fresh evidence from that run (flag-gated, default off); a green re-verification satisfies "a new green test completes" without loosening the gate — evidence still MUST be green and collected after the last candidate write. `test_not_run` and `test_failed` blocks MUST NOT trigger automatic re-verification, and the final JSON result MUST record the re-verification attempt (attempted command, outcome) on both the publication and blocked paths when it occurs.

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
- **THEN** the prior evidence becomes stale and publication remains blocked until a new green test completes — either inside the dev loop, or by the single orchestrator-side re-verification run when enabled

#### Scenario: Stale evidence auto re-verification passes
- **WHEN** the gate blocks with `test_stale`, the auto-triage flag is enabled, and the orchestrator-side re-run of the same test command exits zero
- **THEN** the fresh evidence (collected after all candidate writes) satisfies the gate and publication proceeds, with the attempt recorded in the final JSON result

#### Scenario: Stale evidence auto re-verification fails
- **WHEN** the gate blocks with `test_stale`, the auto-triage flag is enabled, and the re-run exits non-zero or times out (or the flag is off)
- **THEN** publication remains blocked with the `test_stale` result, and the JSON result records the re-verification attempt and outcome when one occurred

#### Scenario: Executor does not merge into main
- **WHEN** the candidate branch has passed verify and would otherwise be ready to merge
- **THEN** the executor does not merge it into main; merge is performed only by the single-flight auto-merge closed loop after its own post-merge verification, never by the executor's publication path

#### Scenario: Red injected acceptance test blocks publication
- **WHEN** an injected acceptance test is among the recognized tests run in the dev worktree and its latest execution exits non-zero
- **THEN** publication is blocked with a `test_failed` result naming the injected test, exactly as for a project-authored test
