## Why

The control-plane standard executor (`dev-agent.py`, ADR-0006) drives every target repository's development loop via the pinned Agent SDK `query()` with a streaming prompt. On 2026-07-27, two admitted PRDs (`custom-mcp-server-url`, `hub-role-pair-view`) both terminated `test_failed` because the dev agent could not run any project-native test command inside the worktree. The dev agent's own logs and an independent sub-agent verification concur: every command that executes Node code (`npm test`, `node --test`, `node -e`, `node <file>`, `node --check`) returns `Tool permission request failed: AbortError: Stream closed` before the process starts, while only `node --version`, `echo`, and read-only `git` are auto-allowed. The dev agent therefore writes code blind, cannot self-verify, and the orchestration-layer verify gate then observes `npm test` exit non-zero. The dev log also records `RuntimeError: aclose(): asynchronous generator is already running`, pointing to an `aclose()` race on the streaming prompt's async generator that closes the SDK stream mid-loop, after which even the `can_use_tool` permission request cannot be sent.

This is independent of the target-plane `scope-bash.cjs` hook (the dev log confirms it already passes): the block originates in the control-plane executor's SDK streaming layer. It breaks the premise of `verified-dev-execution`'s "Verified test evidence before publication" requirement — the executor cannot produce in-loop test evidence if the dev agent cannot run the project's tests at all.

## What Changes

- Diagnose and fix the SDK streaming-prompt `aclose()` race in `Projects/项目推进流水线/scripts/prompt_stream.py` and/or `dev-agent.py` so that the multi-turn tool-call loop keeps the SDK stream open and the `can_use_tool` permission gate stays reachable for every Bash invocation.
- Guarantee the dev agent can execute the target repository's native test command (e.g. `npm test`, `node --test`) inside the worktree without `AbortError: Stream closed`, so the dev loop can self-verify before the orchestration verify gate.
- Add a regression test that reproduces the streaming-prompt `aclose()` race (or its observable symptom — a Node test command executing successfully on a later tool turn) and locks the fix.
- Optionally progress the known SDK follow-up (CLAUDE.md: migrate from string-prompt `query()` to native streaming once `can_use_tool` no longer requires the workaround) if that proves the cleaner durable fix.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `verified-dev-execution`: add an explicit requirement that the control-plane standard executor's SDK dev loop must keep the streaming prompt and `can_use_tool` permission channel open across multi-turn tool calls, so the dev agent can run the project's native test command inside the worktree and produce in-loop test evidence. The existing "Verified test evidence before publication" requirement assumes this executability; this change makes the assumption explicit and enforceable.

## Impact

- Affects the control-plane standard executor in `Projects/项目推进流水线/scripts/dev-agent.py` and `prompt_stream.py` (ADR-0006); target-plane repositories are not touched.
- Adds a regression test under `scripts/test_*` covering streaming-prompt stream longevity / Node test command executability across tool turns.
- No new external service; uses the existing pinned Agent SDK (`>=0.2.121,<0.2.123`) unless the native-streaming migration is chosen.
- No control-plane/target-plane boundary change; no immutable PRD or target-worktree state change.
