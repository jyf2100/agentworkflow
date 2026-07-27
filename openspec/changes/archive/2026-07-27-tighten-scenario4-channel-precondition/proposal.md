## Why

A second-round review of scenario 4 (after the first follow-up `tighten-verified-dev-execution-scenario-evidence`, commit `bd596a2`) found the softened language still overclaims. The scenario now says "directly regression-locking the admitted permission path", but the shipped test (`test_prompt_stream_keeps_stdin_open_until_result`) never exercises the admit branch: FakeTransport emits no control request, `_admit` is never invoked, and no permission response is written. The test asserts only `fake.writes` (initial prompt present) and `not fake.end_input_called`. It directly locks the channel-availability precondition shared by admitted and denied responses — not the admitted outcome itself. "admitted path directly locked" is the same overclaim class, one level down, and the current follow-up cannot close with it present.

## What Changes

- Rewrite scenario 4 THEN to state exactly what the test proves: the deterministic pinned-SDK integration test directly regression-locks that finite prompt input does not close the shared control channel before the result under the default (lifecycle-hooks-disabled) configuration; this locks the channel-availability precondition shared by admitted and denied permission responses and does not directly exercise either permission outcome; path-specific outcome verification and the real Node-command canary remain deferred to natural dispatch verification.
- Documentation-only. No code, no test, no SDK change. Normative MUST unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `verified-dev-execution`: scenario 4 evidence language tightened a second time to match exactly what the test asserts — a channel-availability precondition lock, not an admitted-outcome lock.

## Impact

- Affects only `openspec/specs/verified-dev-execution/spec.md` scenario 4 THEN clause.
- No code/test/dependency change; existing `bash scripts/quality.sh` (1243 passed, 6 xfailed) unaffected.
