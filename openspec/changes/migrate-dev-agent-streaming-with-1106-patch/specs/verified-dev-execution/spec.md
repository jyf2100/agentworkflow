## MODIFIED Requirements

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
