## MODIFIED Requirements

### Requirement: Verified test evidence before publication
The standard executor MUST require structured green test evidence for the current candidate changes before it performs any publication action — commit, push, or pull-request creation. Merging the branch into main is NOT an executor publication action: the executor SHALL NOT merge into main; that destructive side effect is owned by the single-flight auto-merge closed loop, which runs its own post-merge verification against integrated main (see the single-flight-auto-merge capability). The green-evidence gate applies to the candidate branch only.

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
