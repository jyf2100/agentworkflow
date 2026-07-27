# fix-dev-agent-stream-aclose-race — R1 Review

Date: 2026-07-27  
Range: `75e47bd..917fdea`  
Verdict before response: **Request Changes**

## Frozen Acceptance Matrix

| Class | Boundary |
|---|---|
| Must pass | Root-cause claims remain inside available evidence; delta coordinates with `verified-dev-execution`; tasks reproduce and locate the responsible layer before selecting the minimal fix; target-plane `scope-bash` remains excluded |
| Deferred | Final A/B/C implementation and code tests; broader SDK migration unless proved necessary by the current reproducer |
| Out of scope | Correctness of PR #44 itself, target business code, learning memory, unrelated durable-runtime capabilities |
| Follow-up | General SDK upgrades, reusable async-stream abstractions, additional provider/platform compatibility |

## Findings And Resolution

### R1-P1 — Root cause was prematurely fixed on async-generator `aclose`

The observed `aclose(): asynchronous generator is already running` is correlated evidence, not proof of the first cause.
Inspection of pinned SDK `0.2.121` found a stronger candidate: finite `Query.stream_input()` calls
`wait_for_result_and_end_input()`, whose keep-open condition includes SDK MCP servers and hooks but omits
`can_use_tool`. With lifecycle hooks off, stdin can close immediately after the one-message prompt is exhausted,
before a later permission response. SDK `0.2.123` has the same condition, so a bare upgrade is not a fix.

Resolution: proposal/design/tasks now distinguish normal iterable exhaustion, premature `end_input`, concurrent
`aclose`, and client-path behavior. A deterministic pinned-SDK permission request/response reproducer is the primary
RED; no solution is preselected.

### R1-P1 — Permission scenario required denied commands to execute

The delta said a command could be admitted or denied "and executes", contradicting the permission gate.

Resolution: admitted commands must execute and return their real exit status; denied commands must remain unexecuted
and return a structured denial. Both paths must remain free of stream-lifetime errors.

## Validation After Response

```text
OpenSpec strict validation: passed (1/1)
Implementation tests: not run; this range changes specification documents only
CI status: not available for the response commit at review time
Focused evidence: claude-agent-sdk 0.2.121 and 0.2.123 source inspection
```

The R1 documentation findings are closed by this response. Implementation remains red/open until tasks Section 1
produces the required evidence; this review does not claim the runtime defect is fixed.
