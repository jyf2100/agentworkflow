## Why

The development loop records per-run logs and verifier feedback, but lessons remain trapped inside one PRD iteration. Later PRDs can therefore repeat the same planning, repository-discovery, testing, or publication mistakes even when prior runs contain evidence that would have prevented them.

## What Changes

- Generate structured, evidence-linked lesson candidates only after a PRD reaches a terminal outcome and independent verification evidence is available.
- Maintain an append-only per-project candidate history and a rebuildable project lesson catalog outside target repositories and immutable PRDs.
- Promote only actionable, bounded lessons that recur across distinct PRDs, with a narrowly defined exception for independently verified critical invariant violations.
- Retrieve and inject a small set of relevant active lessons into later PRD development prompts.
- Record whether injected lessons were followed and whether the associated failure recurred, then use those outcomes to confirm, downgrade, supersede, or retire lessons.
- Keep reflection and memory failures observable without allowing them to falsify the underlying development, verification, or publication result.

## Capabilities

### New Capabilities

- `cross-prd-learning-memory`: Evidence-grounded candidate extraction, cross-PRD lesson promotion, bounded retrieval, prompt injection, and effectiveness feedback for project-level development memory.

### Modified Capabilities

None.

## Impact

- Affects the control-plane standard executor and dispatch lifecycle in `Projects/项目推进流水线/scripts/dev-agent.py` and `run_daily.py`.
- Extends journal/artifact metadata with reflection and lesson evidence while preserving immutable PRDs and the control-plane/target-plane boundary.
- Adds ignored control-plane state under `.project-auto/state/lessons/`; no lesson state is written into target repositories.
- Adds deterministic schemas, promotion/retrieval policy, prompt-injection limits, tests, and operational visibility.
- Uses the existing pinned Agent SDK and content-addressed artifact store; no new external service is required.
