# Design — tighten-scenario4-channel-precondition

## Context

The first follow-up (`tighten-verified-dev-execution-scenario-evidence`, commit `bd596a2`) softened scenario 4 from "both admitted and denied paths are regression-locked" to "admitted directly locked; denied via shared mechanism". R2 review found this still overclaims: the shipped test never exercises the admit branch — FakeTransport emits no control request, `_admit` is never invoked, and no permission response is written. The test asserts only `fake.writes` (initial prompt present) and `not fake.end_input_called`. It locks the channel-availability precondition shared by admitted and denied responses, not the admitted outcome. Conflating "channel stays available" (precondition) with "admitted path locked" (outcome) is the same overclaim class one level down.

## Decision

Adopt the reviewer's minimal fix verbatim. Scenario 4 THEN states the test directly locks that finite prompt input does not close the shared control channel before the result under the default configuration; this is the precondition shared by admitted and denied responses and does not directly exercise either outcome; path-specific outcome verification and the real Node-command canary remain deferred to natural dispatch verification. No test added — the reviewer concurs the precondition lock is the correct scope; outcome verification stays deferred to dispatch.

## Risk

Two successive softenings of the same scenario may read as erosion. It is precision, not weakening: the normative MUST (channel stays available; admitted starts; denied truthful) is unchanged. Each round moved the evidence description one step closer to what the test actually asserts. Lesson for future sync: a test that checks a precondition must be described in the spec as locking that precondition, not the downstream outcome it enables.
