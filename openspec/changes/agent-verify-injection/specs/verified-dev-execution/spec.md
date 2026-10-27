## ADDED Requirements

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
- **THEN** the orchestrator's stream matching uses tail-segment semantics (the same exit-code-trust function as the dev-side evidence collector) and the tool_result exit code is admitted as verdict evidence
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

## MODIFIED Requirements

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
