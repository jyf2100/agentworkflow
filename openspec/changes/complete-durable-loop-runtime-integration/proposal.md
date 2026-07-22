## Why

The archived `add-durable-loop-runtime` change added substantial journal, retry, hook, sandbox, and telemetry modules, but the production dispatch path still runs the legacy loop with only optional shadow journaling. The system must now complete the cutover safely, correct false terminal states and evidence-integrity failures, and provide real quality/canary evidence before claiming durable runtime readiness.

## What Changes

- Wire journal reduction, session-aware retry, lifecycle hooks, sandbox selection, and telemetry into the real `run_daily.py` and `dev-agent.py` execution paths behind staged feature flags.
- Make PRD input immutable for new iterations; persist verifier feedback and recovery context as journal-referenced artifacts with per-round iteration IDs.
- Require semantic verification success as well as independent green tests before emitting `published` or allowing publication.
- Make artifact/journal persistence failures visible and fail closed at evidence and recovery boundaries.
- Enforce container network policy at the runtime boundary, or explicitly report the lower assurance level when enforcement is unavailable.
- Complete journal mappings, recovery tooling, real SDK/container canaries, crash drills, and reproducible quality evidence before cutover.
- **BREAKING**: journal-driven terminal states and reports become authoritative for new runs after shadow parity; legacy JSON remains read-compatible for one release cycle only.

## Capabilities

### New Capabilities

- `durable-runtime-integration`: Connects the existing runtime modules to production dispatch and SDK execution with staged cutover and legacy fallback.
- `verified-publication-integrity`: Prevents false `published` states and requires complete, integrity-checked evidence across test, semantic verify, journal, and artifact boundaries.
- `runtime-cutover-evidence`: Defines real quality, canary, crash-recovery, sandbox, telemetry, and operator-recovery evidence required for rollout.

### Modified Capabilities

None. The existing four durable-loop capabilities remain the behavioral source contracts; this change closes their production integration and acceptance gaps.

## Impact

- `Projects/项目推进流水线/scripts/run_daily.py`: real journal-driven dispatch, retry decisions, reconciliation, terminal state emission, and report fields.
- `Projects/项目推进流水线/scripts/dev-agent.py`: SDK hook registration, session metadata, sandbox execution, and evidence failure handling.
- Existing runtime modules: journal, loop state, retry policy, hooks, sandbox, telemetry, artifact store, and compatibility readers.
- New integration and canary tests; reproducible quality evidence and operator recovery tooling.
- Runtime configuration/profile contracts for feature flags, network enforcement, artifact/journal locations, and rollout state.
