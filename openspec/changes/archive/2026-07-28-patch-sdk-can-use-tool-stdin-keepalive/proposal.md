# Proposal — patch-sdk-can-use-tool-stdin-keepalive

> **⚠ Superseded by `migrate-dev-agent-streaming-with-1106-patch` (2026-07-28).**
> C3（0.2.121 monkey-patch 思路）被推翻：源码核验证实 (1) 升级 0.2.128 不解 #1105（保活条件与
> 0.2.121 一字不差）；(2) C3「canary deferred to natural dispatch」是空头承诺（自然 dispatch 3 轮全 RED，
> 承诺永不兑现）。新 change 改用 ast 变异原方法体（H3-patch，零漂移、#1103 字节级保留）+ canary 落 CI
> required check（堵空头承诺）。未完成 tasks 标 `superseded`，不再实施。

> Follow-up to archived `2026-07-27-fix-dev-agent-stream-aclose-race` (Plan A). Plan A patched the **input side** (`prompt_stream.py` stays pending); it goes GREEN in the fake-transport unit layer but **still RED under the real pinned SDK 0.2.121** (2026-07-27 cc-web-control dispatch, 3 dev-loop rounds all `test_failed`; the xfail-strict `test_sdk_query_keeps_stdin_open_until_result` still locks the upstream defect). This change patches the **SDK method side** instead — the actual site of the upstream bug.

## Why

The control-plane standard executor (`dev-agent.py`, ADR-0006) drives every target repo's dev loop via `query(prompt=prompt_stream(prompt), options)` with `can_use_tool` as the Bash permission gate. On 2026-07-27, two admitted PRDs (`custom-mcp-server-url`, `hub-role-pair-view`) still terminated `test_failed`: every Node-executing command (`npm test`, `node --test`, `node -e`, …) returned `Tool permission request failed: AbortError: Stream closed` before the process started, while `node --version` / read-only `git` were auto-allowed. The dev agent writes code blind, cannot self-verify, and the verify gate observes `npm test` exit non-zero.

**Root cause (confirmed by source inspection of SDK 0.2.121 + upstream tracker):**

- `_internal/query.py:819` — `wait_for_result_and_end_input()` keeps stdin open only `if self.sdk_mcp_servers or self.hooks:`. The `can_use_tool` callback is **omitted** from this keep-alive whitelist, even though its permission response is written back over the **same stdin** (`_send_control_request`, query.py:384-435).
- With lifecycle hooks disabled (production default), once the finite `AsyncIterable` prompt is exhausted, `stream_input()` (query.py:841) calls `wait_for_result_and_end_input()` → `transport.end_input()` closes stdin → the next `can_use_tool` permission response cannot be written → `AbortError: Stream closed`.
- First-turn probes (`node -v`) pass because stdin is still open; later-turn commands (`npm test`) fail because stdin is already closed.

**Why Plan A is insufficient:** Plan A keeps `prompt_stream` pending (`await asyncio.Event().wait()`) so `stream_input` never exhausts and never calls `wait_for_result_and_end_input`. This is correct on the main path but does not cover secondary stdin-closure paths inside the SDK; under the real SDK 0.2.121 the defect still reproduces. Upstream confirms this is an open bug: **[anthropics/claude-agent-sdk-python#1105](https://github.com/anthropics/claude-agent-sdk-python/issues/1105)** — *"can_use_tool callback never invoked without hooks/MCP servers: stdin closes before permission requests arrive"* — state **OPEN**, no milestone, zero comments, untouched since 2026-07-11. The 0.2.127 fix #1103 covers a *different* scenario (background tasks in flight) and does **not** close #1105.

This breaks the premise of `verified-dev-execution`'s "Dev-agent test command executability across the SDK dev loop" requirement (added by Plan A): the executor cannot produce in-loop test evidence if the dev agent cannot run the project's tests.

## What Changes

- **Monkey-patch the SDK method site** (the actual defect location): at executor startup, patch `claude_agent_sdk._internal.query.Query.wait_for_result_and_end_input` so its keep-alive condition becomes `self.sdk_mcp_servers or self.hooks or self.can_use_tool`. This directly fixes the omitted-`can_use_tool` whitelist at the source, rather than working around it on the input side.
- **Version guard**: the patch is applied only for SDK versions where the defect is present (0.2.121–0.2.122). On load, inspect the SDK's actual `wait_for_result_and_end_input` condition: if it already covers `can_use_tool` (upstream fixed #1105), skip the patch and log; if the SDK version/structure is unknown, fail loud (do not silently patch an unrelated method).
- **Flip the regression lock**: `test_dev_agent_stream_lifespan.py::test_sdk_query_keeps_stdin_open_until_result` is currently `xfail(strict)` locking the upstream defect RED. With the patch applied at module load, the SDK method now keeps stdin open → the test PASSES → strict mode forces removal of the `xfail` marker (prevents the defect from silently going green, per the xfail-strict-regression-lock discipline). This is the patch's primary acceptance signal.
- **Real dev-loop canary**: re-dispatch cc-web-control `custom-mcp-server-url` / `hub-role-pair-view`; the dev agent must run `npm test` inside the worktree and return a real exit status (no `AbortError: Stream closed`).
- **Upstream tracking**: post the control-plane reproduction (string-prompt + `can_use_tool` + no hooks/MCP → Stream closed; source-located at query.py:819) as a comment on #1105 to help upstream fix it, and subscribe. When #1105 ships in an SDK release, raise the version pin and remove the patch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `verified-dev-execution`: **no new requirement** — Plan A already added "Dev-agent test command executability across the SDK dev loop" to the main spec. This change swaps the *implementation* that satisfies it (SDK method-side monkey-patch instead of input-side pending workaround) and tightens one Scenario to require a version-guarded, removable patch rather than a particular iterator trick. The requirement text is unchanged.

## Impact

- Affects `Projects/项目推进流水线/scripts/dev-agent.py` (patch application at startup) and/or a new zero-dependency `sdk_compat_patch.py` module; `scripts/test_dev_agent_stream_lifespan.py` (xfail → strict-pass flip).
- `prompt_stream.py` (Plan A) is left in place as a benign input-side redundancy; C3 is the SDK method-side root fix. Both coexist without conflict.
- No SDK version-pin change (`>=0.2.121,<0.2.123`); the patch is a runtime monkey-patch, not a dependency change.
- Risk: monkey-patching a third-party internal method is fragile across SDK upgrades → mitigated by the version/structure guard + #1105 tracking + the flipped xfail-strict test that fails loud if the patch silently stops applying.
- No control-plane/target-plane boundary change; no immutable PRD or target-worktree state change.
