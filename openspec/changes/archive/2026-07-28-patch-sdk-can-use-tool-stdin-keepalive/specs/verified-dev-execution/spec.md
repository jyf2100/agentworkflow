## MODIFIED Requirements

### Requirement: Dev-agent test command executability across the SDK dev loop

The control-plane standard executor's SDK dev loop MUST keep the bidirectional control channel required by `can_use_tool` available until the dev loop reaches a result, so that the development agent can request the target repository's native test command (for example `npm test` or `node --test`) inside the worktree at any tool turn. An admitted command MUST start and return its real exit status; a denied command MUST remain unexecuted and return the permission gate's structured denial. Neither path may fail because the streaming input or control channel was closed early. The requirement constrains observable executability and permission truthfulness, not a particular async-generator or iterator implementation.

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

- **WHEN** the executor's test suite runs with a version-guarded compatibility patch applied at module load that extends the pinned SDK's `wait_for_result_and_end_input` keep-alive condition to cover `can_use_tool`
- **THEN** the deterministic pinned-SDK integration test that previously locked the upstream defect as indefinite `xfail(strict)` MUST now pass strict (the `xfail` marker is removed), so that a regression in the patch — or an SDK upgrade that silently drops the keep-alive — fails loud instead of silently closing the channel; the patch MUST refuse to apply when the SDK method's structure no longer matches the known-defective shape (fail-loud version guard), and MUST skip cleanly when the SDK already covers `can_use_tool` upstream. This closes the gap that previously deferred the real executability verification to natural dispatch verification, which itself stayed RED across the 2026-07-27 dispatches.
