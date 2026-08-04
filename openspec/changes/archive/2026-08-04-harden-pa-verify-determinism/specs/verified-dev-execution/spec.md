## ADDED Requirements

> Scope note: `pa-verify`'s full output contract (`verdict` / `feedback_section` / `round`) remains owned by `.claude/agents/pa-verify.md`. This delta only anchors the location element (mechanical line anchors) and the bundle-scoped review trigger within `verified-dev-execution`'s "verify dev output" capability — the semantic half that the base spec's executor-focused Purpose did not yet cover. Base Purpose widening is a sync-stage concern, not part of this delta.

### Requirement: Line-anchored verify feedback

When `pa-verify` returns `verdict=revise`, the location element of its `feedback_section` MUST be grounded in mechanically-derived anchors produced by orchestrator logic — the failing test name, the failing file and assertion line parsed from the independent verify output, and the corresponding diff hunk — not locations the model recalls on its own. Anchors are carried in a structured sub-field of `feedback_section`; the model reasons about cause and fix, location is anchored. First-supported runners are `jest` (Node) and `pytest` (Python); any other runner output hits the unresolved path below.

#### Scenario: Red test maps to a diff hunk

- **WHEN** the independent verify run reports a failing test and the orchestrator can parse its failing file and line from the test output (jest or pytest format)
- **THEN** the orchestrator mechanically derives `(test name, failing file:line, matching diff hunk)` and injects them as a structured anchor sub-field in `feedback_section`; the structured anchor field is the machine-asserted contract (the persona cites the injected anchors rather than recalling locations — persona prose compliance is design guidance, not a scenario THEN)

#### Scenario: Anchor cannot be resolved

- **WHEN** the mechanical anchor mapping cannot resolve a failing test to a diff hunk (unparseable test output, unsupported runner, or no hunk matches)
- **THEN** the structured anchor sub-field carries an `unresolved` flag with a reason, and the orchestrator records the gap in the dispatch record (the persona does not fabricate a location — prose compliance is design guidance)

#### Scenario: Anchors recomputed each round

- **WHEN** round ≥ 2 incremental redo produces a new failing test against a new base (the previous dev branch)
- **THEN** the anchors are recomputed against the new diff and fresh test output, never reused from the prior round

#### Scenario: Round ≥ 2 red assertion lands in the base, not the incremental diff

- **WHEN** round ≥ 2 incremental redo changes area A but the failing assertion is on a line that existed in the previous dev branch (base) and is outside the incremental diff
- **THEN** the orchestrator marks the anchor `base-side regression at <file:line>`, distinguished from `unresolved` (which means parse failure), so the revise feedback preserves the regression-location signal instead of degrading to unresolved

### Requirement: Bundle-scoped review coverage for large diffs

When the dev diff exceeds a configured threshold, the orchestrator MUST split it into related-file bundles and feed them to `pa-verify` with isolated per-bundle context. Bundling itself applies to both the green and revise paths; **per-criterion coverage accounting applies only to the revise path**. The green-path contract ("quick-confirm → pass on no major off-target") is unchanged — bundling only splits the glance into per-bundle quick sanity, it does not deepen green-path review into exhaustive accounting.

#### Scenario: Large diff is bundled on both paths

- **WHEN** the dev diff exceeds the configured threshold (file count or total diff lines), regardless of test color
- **THEN** the orchestrator groups related files into bundles (for example an implementation file together with its test and collocated i18n/config) and feeds them to `pa-verify` with isolated per-bundle context — on both the green and the revise path

#### Scenario: Revise-path acceptance-criterion coverage accounting

- **WHEN** `pa-verify` reviews a bundled diff on the revise path (test red)
- **THEN** the orchestrator feeds each bundle with its mapped PRD acceptance criteria, and `pa-verify`'s revise `feedback_section` carries a structured `criteria_coverage` field (per criterion: covered / not-covered-in-bundle) — this structured field is the machine-asserted contract; the persona's prose accounting depth is design guidance

#### Scenario: Green-path quick sanity per bundle

- **WHEN** `pa-verify` reviews a bundled diff on the green path (`test_rc=0`)
- **THEN** the orchestrator feeds bundles **without** per-criterion acceptance-criteria mapping, and `feedback_section` carries no `criteria_coverage` field on the green path (the persona does only per-bundle quick sanity and returns `verdict=pass` on no major off-target — persona quick-confirm behavior is design guidance, not a scenario THEN)

#### Scenario: Small diff stays single-pass

- **WHEN** the dev diff is at or below the configured threshold
- **THEN** single-bundle review is used and no bundle-splitting overhead is incurred
