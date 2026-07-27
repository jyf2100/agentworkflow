## ADDED Requirements

### Requirement: Dev-agent test command executability across the SDK dev loop

The control-plane standard executor's SDK dev loop MUST keep the streaming prompt and the `can_use_tool` permission channel open across every multi-turn tool invocation, so that the development agent can execute the target repository's native test command (for example `npm test`, `node --test`, or the project-configured `test_cmd`) inside the worktree at any tool turn. The executor MUST NOT permit an `aclose()` race on the streaming prompt's async iterable — or any other streaming-lifetime defect — to close the SDK stream mid-loop, because once the stream closes the `can_use_tool` permission request fails with `AbortError: Stream closed` and the agent can no longer run any command that executes code. That silently converts every in-loop self-verification attempt into a non-actionable failure and leaves the orchestration verify gate as the only signal, which contradicts the executor's purpose of producing truthful in-loop test evidence. The executor MUST be verifiably able to run the project's test command at any tool turn, not only on the first turn or only for version-only probes.

#### Scenario: Dev agent runs the project test command on a later tool turn

- **WHEN** the dev loop has already completed one or more tool calls and the agent invokes the project's native test command (for example `npm test`) inside the worktree
- **THEN** the command reaches the `can_use_tool` permission gate, is admitted or denied on its merits, and executes — it MUST NOT fail with `AbortError: Stream closed` solely because the streaming prompt's async iterable was closed mid-loop

#### Scenario: Streaming prompt async iterable is not closed mid-loop

- **WHEN** the SDK consumes the streaming prompt across multiple tool turns
- **THEN** the executor keeps the prompt async iterable / stream alive for the duration of the dev loop, and an `aclose()` on the iterable does not race with an in-flight iteration to produce `RuntimeError: aclose(): asynchronous generator is already running`

#### Scenario: Regression locks the executability fix

- **WHEN** the executor's test suite runs
- **THEN** a regression test reproduces the streaming-prompt `aclose()` race or its observable symptom (a Node test command executing successfully on a later tool turn) and fails if the stream is closed mid-loop again
