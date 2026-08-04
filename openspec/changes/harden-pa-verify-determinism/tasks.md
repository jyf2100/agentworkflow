## 1. Mechanical anchor mapping (orchestrator)

- [x] 1.1 Parse the `independent_verify` `.testout` to extract `(failing test name, failing file, failing assertion line)`; ship `jest` (Node) and `pytest` (Python) parsers first (first-supported runners); **wire the dispatcher to select the mapper by the discovered `test_cmd` type**. Multiple failing tests: anchor all of them (configurable cap, default = all)
- [x] 1.2 Map each failing `file:line` to its diff hunk by reading the `_dump_branch_diff` output; on round ≥ 2, when the red line lands in the base (outside the incremental diff), mark the anchor `base-side regression at <file:line>` — distinct from `unresolved`
- [x] 1.3 Fail-open on unresolvable anchors: unparseable output / unsupported runner / no hunk match → structured anchor field carries `unresolved` + reason (never fabricate); record the gap in the dispatch record (`rec["verify_anchors"]`). Same fail-safe posture as `fail-safe-dispatch`'s `UNKNOWN`
- [x] 1.4 Recompute anchors each round: on round ≥ 2, derive against the new base diff and fresh `.testout`; never reuse round-1 anchors

## 2. Structured feedback contract

- [x] 2.1 **Embed** anchors as a structured sub-field **alongside** the existing markdown location text (markdown stays as `dev` reads it today; structured anchors ride within the same `feedback_section`; `dev-agent.py` does not parse `feedback_section` — backward-compatible, no target-plane change)
- [x] 2.2 Update `.claude/agents/pa-verify.md`: consume orchestrator-injected anchors, cite them for location instead of recalling; add a lightweight self-check (reflection) of the "how to fix" guidance on the `revise` path only (resolves OQ-Q2 to revise-always)
- [x] 2.3 Persona must carry the `unresolved` / `base-side` flag from the structured field into the feedback (no fabrication) — prose compliance is design guidance; the structured sub-field is the machine-asserted contract

## 3. Bundle-scoped review (bundling both paths; accounting revise-only)

- [x] 3.1 Implement bundle splitting: configurable threshold (starting `>10` changed files or `>800` diff lines, tune empirically) with related-file grouping (implementation + its test + collocated i18n/config)
- [x] 3.2 Keep single-bundle review for diffs at/below the threshold (no splitting overhead)
- [x] 3.3 Orchestrator feeds bundles to `pa-verify` with isolated per-bundle context **on both green and revise paths** (bundling is color-independent mechanics) — 实现：prompt 内逐-bundle 边界（`_render_bundle_block` + `verify_prompt` bundle 块）；真物理 multi-call 列 follow-up（pa-verify 测试输出本就全局读，multi-call N 倍成本 + verdict 合并不划算）
- [x] 3.4 **Revise path only**: orchestrator feeds each bundle with its mapped PRD acceptance criteria; `pa-verify`'s revise `feedback_section` carries a structured `criteria_coverage` field (per criterion: covered / not-covered-in-bundle) — 实现：`verify_prompt` bundle_guidance 指引 persona revise 产 criteria_coverage（字段内容正确性 deferred golden suite）
- [x] 3.5 **Green path**: `pa-verify` does per-bundle quick sanity only (no exhaustive per-criterion accounting, no `criteria_coverage` field) and returns `pass` on no major off-target — green quick-confirm contract preserved — 实现：bundle_guidance 指引 green quick-sany；green-path prompt 形态不变（方向 A）

## 4. Feature flag + flag-gated rollout (not shadow-parity)

- [x] 4.0 Add a new flag to `LoopFlags` + `FLAGS_ENV_MAP` in `scripts/feature_flags.py` (precedent: `cross_prd_learning_*`; `FLAGS_ENV_MAP` is a stable external contract — pick an env-var name + domain cut); flag-off = current behavior
- [x] 4.1 Wire the verify sub-loop to read the flag; flag-off = single-pass free-form feedback (current path)
- [ ] 4.2 **Manual verdict audit (NOT shadow-parity cutover)** — **deferred 到 rollout**（非代码 task）：flag 开后审 N≥3 真实 dispatch records（comparing revise-feedback actionability with/without anchors+bundles）；persist a parity manifest with input-record digest + observed verdict drift + anchor-mismatch count; a self-attested boolean is explicitly **not** evidence (lesson from `complete-durable-loop-runtime-integration` tasks-review P0-2/P1-2)
- [x] 4.3 Verify rollback: flag off reverts to the current single-pass, free-form-feedback path (wiring test `test_derive_flag_off_returns_empty` + baseline prompt assertion)

## 5. Tests (hermetic, against Node/Python fixtures)

- [x] 5.1 (a) Add red-producing fixtures (`assert.ok(false)` for Node, `assert False` for Python — existing `reproducible-pipeline-validation` fixtures are green-only); (b) anchor-mapping unit tests: `jest` red → correct anchors, `pytest` red → correct anchors, unparseable → `unresolved`
- [x] 5.2 Bundle-splitting unit tests: threshold boundary (above/below/equal) and related-file grouping correctness
- [x] 5.3 Structured-feedback contract: anchor sub-field present + dev-facing markdown backward-compatible; revise-path `criteria_coverage` field present; green-path requires no `criteria_coverage` (wiring tests cover anchor injection + baseline)
- [x] 5.4 Round ≥ 2 anchor-recompute + base-side regression marking (failing line in base → `base-side` flag, not `unresolved`)
- [x] 5.5 Green-path quick-sanity: a bundled green diff yields `pass` without exhaustive accounting (green contract preserved) — `test_verify_prompt_single_bundle_is_baseline` + green-path prompt 指引；persona green 行为正确性 deferred golden suite
- [x] 5.6 `bash scripts/quality.sh` green (compileall + pytest + ruff) from the `Projects/项目推进流水线` root

## 6. Docs sync

- [x] 6.1 Update `Projects/项目推进流水线/SPEC.md` §4.4 to describe the new verify input shape (anchors + bundles; revise-only coverage accounting)
- [x] 6.2 Note in `.claude/agents/pa-verify.md` / design memo: architecture inspired by `alibaba/open-code-review`; document the deliberate Recall philosophical fork (test gate stays high-Recall backstop; comment-noise trade-off does not migrate to structured feedback)
- [x] 6.3 Write back `SPEC.md` §4.5 persona count (3 → 4; `pa-verify` is already the 4th semantic persona) — `pa-verify`'s ADR anchor is the mechanical-vs-semantic split (design D6), not ADR-0005's report-stage text
