## Why

The control-plane standard executor (`dev-agent.py`, ADR-0006) drives every target repository's development loop via the pinned Agent SDK `query()` with a streaming prompt. On 2026-07-27, two admitted PRDs (`custom-mcp-server-url`, `hub-role-pair-view`) both terminated `test_failed` because the dev agent could not run any project-native test command inside the worktree. The dev agent's own logs and an independent sub-agent verification concur: every command that executes Node code (`npm test`, `node --test`, `node -e`, `node <file>`, `node --check`) returns `Tool permission request failed: AbortError: Stream closed` before the process starts, while only `node --version`, `echo`, and read-only `git` are auto-allowed. The dev agent therefore writes code blind, cannot self-verify, and the orchestration-layer verify gate then observes `npm test` exit non-zero. The dev log also records `RuntimeError: aclose(): asynchronous generator is already running`; this is a correlated streaming-lifecycle symptom, not yet proof that async-generator `aclose()` is the first cause.

This is independent of the target-plane `scope-bash.cjs` hook (the dev log confirms it already passes): the block originates in the control-plane executor's SDK streaming/control layer. Source inspection of the pinned SDK `0.2.121` shows a stronger root-cause candidate: after a finite `AsyncIterable` prompt is exhausted, `Query.stream_input()` calls `wait_for_result_and_end_input()`, which keeps stdin open for SDK MCP servers or hooks but not for `can_use_tool`. With lifecycle hooks disabled, stdin may therefore close before a later permission response can be written. Tasks Section 1 must reproduce and distinguish this control-channel lifetime defect from the correlated `aclose()` exception before selecting a fix. The failure breaks the premise of `verified-dev-execution`'s "Verified test evidence before publication" requirement — the executor cannot produce in-loop test evidence if the dev agent cannot run the project's tests at all.

## What Changes

- Diagnose and fix the SDK streaming/control-channel lifetime defect in `Projects/项目推进流水线/scripts/prompt_stream.py`, `dev-agent.py`, and/or the selected SDK integration path so that the `can_use_tool` permission channel stays reachable until the dev loop reaches a result. Treat the observed `aclose()` exception as a hypothesis to prove or disprove, not as the preselected implementation target.
- Guarantee the dev agent can execute the target repository's native test command (e.g. `npm test`, `node --test`) inside the worktree without `AbortError: Stream closed`, so the dev loop can self-verify before the orchestration verify gate.
- Add a deterministic pinned-SDK integration regression that proves a later `can_use_tool` request can receive and write its response before stdin closes, plus a real dev-loop canary showing a later Node test command starts successfully.
- Optionally progress the known SDK follow-up only if the candidate SDK/client path is first proven to keep the permission control channel open. Source inspection confirms that upgrading from `0.2.121` to `0.2.123` alone does not change the relevant `wait_for_result_and_end_input()` condition and is not a fix by itself.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `verified-dev-execution`: add an explicit requirement that the control-plane standard executor's SDK dev loop must keep its bidirectional permission control channel available across multi-turn tool calls, so the dev agent can run the project's native test command inside the worktree and produce in-loop test evidence. The existing "Verified test evidence before publication" requirement assumes this executability; this change makes the assumption explicit and enforceable without prescribing one prompt-iterator implementation.

## Impact

- Affects the control-plane standard executor in `Projects/项目推进流水线/scripts/dev-agent.py` and `prompt_stream.py` (ADR-0006); target-plane repositories are not touched.
- Adds a regression test under `scripts/test_*` covering streaming-prompt stream longevity / Node test command executability across tool turns.
- No new external service; uses the existing pinned Agent SDK (`>=0.2.121,<0.2.123`) unless the native-streaming migration is chosen.
- No control-plane/target-plane boundary change; no immutable PRD or target-worktree state change.
