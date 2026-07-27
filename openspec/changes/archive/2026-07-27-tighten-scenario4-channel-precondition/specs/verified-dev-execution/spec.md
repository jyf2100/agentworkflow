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

- **WHEN** the executor's test suite runs
- **THEN** a deterministic pinned-SDK integration test directly regression-locks that finite prompt input does not close the shared control channel before the result under the default (lifecycle-hooks-disabled) configuration — this locks the channel-availability precondition shared by admitted and denied permission responses, and does not directly exercise either permission outcome; path-specific outcome verification and the real Node-command canary remain deferred to natural dispatch verification
