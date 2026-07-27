## Why

A retrospective expert review of the archived change `fix-dev-agent-stream-aclose-race` (3 independent reviewers, 2026-07-27) found that scenario 4 of the newly synced requirement "Dev-agent test command executability across the SDK dev loop" (`openspec/specs/verified-dev-execution/spec.md:74-77`) overstates the evidence the shipped test suite actually provides. The scenario's THEN clause asserts three things the implementation does not back:

1. "a dev-loop canary proves an admitted Node test command on a later tool turn actually starts" — no such real dev-loop canary was run; it was deferred to natural dispatch verification (design §6, tasks §1.3/§3.1 of the archived change).
2. "both admitted and denied permission paths are regression-locked" — only the admitted path is directly regression-locked (`test_prompt_stream_keeps_stdin_open_until_result` uses `_admit`). The denied path is covered by shared-mechanism reasoning (tasks §2.3: admit and deny both reach the same `transport.write` via `_handle_control_request` query.py:415-430), not by a direct `PermissionResultDeny` test.
3. The shipped `_admit` callback is never actually invoked (FakeTransport emits no control request, no CLI on the other end), so a denied-path test would be functionally redundant with the admitted one — reviewers concurred no direct denied test is needed. But the permanent spec must not claim a direct lock that does not exist.

The design and tasks artifacts of the archived change are honest about this; the honesty simply did not carry into the permanent synced spec. A future reader of only the spec would expect a real dev-loop canary and a direct denied-path regression lock that do not exist. This change tightens the permanent contract to match the shipped evidence. Two reviewers confirmed no new test is required; one flagged the spec-vs-evidence gap. This change adopts the consensus: align the language, do not add tests.

## What Changes

- Rewrite scenario 4 of "Dev-agent test command executability across the SDK dev loop" so its THEN clause matches the shipped evidence: the admitted path is directly regression-locked by the deterministic pinned-SDK integration test; the denied path is covered by the shared `can_use_tool` response-write mechanism (channel stay-alive is path-agnostic, since admit and deny both reach the same `transport.write`); the real dev-loop Node-command canary is explicitly deferred to natural dispatch verification.
- Documentation-only change: no code, no test, no SDK version, no control-plane/target-plane boundary change. The normative claim of the requirement is unchanged — only the evidence/coverage description in scenario 4 is made truthful.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `verified-dev-execution`: tighten scenario 4 of "Dev-agent test command executability across the SDK dev loop" to align the regression-coverage language with the actually shipped evidence (admitted path directly locked; denied path via shared mechanism; real canary deferred to natural dispatch verification). No requirement is added, removed, or weakened in its normative MUST claim — only the evidence description is corrected.

## Impact

- Affects only `openspec/specs/verified-dev-execution/spec.md` (scenario 4 THEN clause).
- No code, no test, no dependency, no boundary change. The underlying fix (`prompt_stream.py`) and its regression locks are unchanged and remain green (`bash scripts/quality.sh`: 1243 passed, 6 xfailed).
