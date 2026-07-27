# Design — tighten-verified-dev-execution-scenario-evidence

## Context

The archived change `fix-dev-agent-stream-aclose-race` (synced 2026-07-27, commit `74894c5`) added the requirement "Dev-agent test command executability across the SDK dev loop" to `verified-dev-execution`. A retrospective 3-reviewer expert review on 2026-07-27 found scenario 4 of that requirement overstates the shipped evidence in three ways (see `proposal.md`). Two reviewers concluded no new test is needed; one flagged the spec-vs-evidence gap. This change aligns the permanent spec language with the shipped evidence.

## Goals / Non-Goals

**Goals**
- Make scenario 4's THEN clause truthful: every coverage claim maps to something the test suite or the change design actually establishes.
- Preserve the normative MUST claim of the requirement unchanged (executability + permission truthfulness across the dev loop).

**Non-Goals**
- Do NOT change `prompt_stream.py`, `dev-agent.py`, or any test.
- Do NOT add a `PermissionResultDeny` direct regression test (reviewers concurred it is redundant: the shipped `_admit` callback is never invoked because FakeTransport emits no control request; admit and deny share the same `transport.write` path).
- Do NOT run a manual real dev-loop canary (deferred to natural dispatch verification; cost CRITICAL $96.27 at review time).
- Do NOT upgrade the SDK pin.

## Decisions

### D1: Soften the language (reviewer option a), do not add tests (reviewer option b)

Expert 3 proposed two paths: (a) soften the spec to match evidence, or (b) keep the strong spec and add a denied-path test + real dev-loop canary. Expert 2 independently established that a denied-path test is functionally redundant (the `_admit` callback is never actually invoked in the test; swapping `_deny` would behave identically). Therefore (b)'s denied test adds no coverage, and (b)'s real canary is a cost-bearing manual step with no additional determinism signal beyond what the L1 pinned-SDK integration test already proves at the SDK interface boundary. (a) is the consensus choice: align language, add no tests.

### D2: The real dev-loop canary stays deferred to natural dispatch verification

The underlying fix is already deployed in `prompt_stream.py`; the next cron dispatch (03:17) will exercise the real dev loop and the verify gate will observe whether the dev agent can self-test. This is the established fallback path recorded in the archived change's design §6. No reason to force a manual canary here.

## Risks

- **Perceived weakening of the contract.** Softening "both admitted and denied paths are regression-locked" to "admitted directly locked; denied via shared mechanism" may read as a weaker guarantee. It is not: the normative MUST (channel stays available; admit starts; deny stays truthful) is unchanged. Only the evidence description in scenario 4 is corrected. If a future change adds a direct denied-path test or a real canary, the language can be strengthened again.
- **Spec drift from artifacts.** This change exists precisely because the archived change's design/tasks were honest but the honesty did not carry into the synced spec. The lesson: when a change defers evidence (e.g., canary to dispatch), the synced spec scenario must reflect the deferral, not the aspiration.
