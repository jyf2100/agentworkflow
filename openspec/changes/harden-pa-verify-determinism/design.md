## Context

`pa-verify` is the semantic half of dev-output verification: it reads the dev diff + the independent-verify test output and judges "green but off-target" work, emitting `pass` or `revise` with a `feedback_section`. The mechanical half (the executor's `test_rc=0` gate and truthful JSON result) is already specced by `verified-dev-execution`; the semantic half is not, and today it is driven purely by prompt. Two gaps follow:

- The `feedback_section` location element (which file / which test / which assertion line) is free-form text the model composes — on large diffs the cited location drifts, and `dev` cannot act on it reliably.
- `pa-verify` "quickly confirms the diff roughly matches the PRD acceptance criteria" — on large diffs the LLM selectively reviews part of the changeset and misses the rest.

`alibaba/open-code-review` (Apache-2.0; README verified line-by-line 2026-08-04, provenance pinned in References) names the shared root cause — *"a purely language-driven architecture lacks hard constraints on the review process"* — and ships a deterministic-engineering × agent hybrid: independent `comment-positioning` + `comment-reflection` modules, and `smart file bundling` (divide-and-conquer, isolated per-bundle context, naturally concurrent). This change ports that philosophy onto `pa-verify` while keeping the test gate as the hard skeleton.

Current state references: `pa-verify` persona at `.claude/agents/pa-verify.md`; verify sub-loop in `Projects/项目推进流水线/scripts/run_daily.py` `stage_dispatch` (design memo `docs/verify-commit-loop-design.md`); `independent_verify` already drops a clean `.testout` and `_dump_branch_diff` drops the diff for `pa-verify` to Read.

**Green-path review shape is unchanged by this change.** `pa-verify`'s existing green-path contract ("quick-confirm → pass on no major off-target") is preserved exactly: the deterministic additions (mechanical anchors, per-bundle bundling) layer on top, and per-criterion coverage accounting is **revise-path only**. What changes is the revise feedback's anchorability/coverage, not how lenient a green pass is.

## Goals / Non-Goals

**Goals:**

- Make `pa-verify`'s revise-feedback location anchorable and auditable — derived mechanically, not recalled by the model (Requirement: Line-anchored verify feedback).
- Make large-diff **revise-path** review cover every acceptance criterion by splitting related files into isolated-context bundles; large-diff **green-path** only gets per-bundle quick sanity (Requirement: Bundle-scoped review coverage for large diffs).
- Anchor the semantic half of dev-output verification in `verified-dev-execution` so it is regressible, not left to prompt discretion.

**Non-Goals:**

- Integrating the `open-code-review` CLI into the pipeline — architecture inspiration only, no new dependency.
- Changing test-gate semantics — `test_rc=0` stays the hard pass backbone; Recall is **not** lowered (see D5).
- **Changing the green-path review shape** — green stays quick-confirm → pass; bundling only splits the glance, it does not deepen green-path review into exhaustive accounting.
- Relaxing `pa-verify`'s禁区 — it still never edits project code / commits / opens PRs (ADR-0002).
- Building a golden judgement regression suite for the persona itself (meta-layer follow-up, tracked separately). Persona prose compliance (e.g. "the persona cites anchors rather than recalls") is therefore design guidance, not a machine-asserted scenario THEN — the scenarios assert the structured sub-fields the orchestrator injects/records.
- Making reflection a separate hard gate — it stays a lightweight self-check inside the revise path only.

## Decisions

**D1 — Anchors computed by the orchestrator, not by the persona.** The orchestrator already holds the diff and the `.testout`; it parses `failing test → file:line → diff hunk` deterministically and injects anchors into the `pa-verify` prompt as a structured sub-field. *Alternative considered:* give `pa-verify` a `Grep`/`Glob` tool to locate itself — rejected: it re-introduces prompt-driven location, adds persona turns, and the persona toolset is deliberately `Read`-only. The orchestrator is the deterministic half; the persona is the semantic half.

**D2 — Unresolved anchors fail open with an explicit flag, never a fabricated location; base-side regressions are distinguished from parse failures.** When the test output is unparseable, the runner is unsupported, or no hunk matches, the structured anchor field carries `unresolved` with a reason and the gap is recorded in the dispatch record. On round ≥ 2, when the red assertion lands on a base line outside the incremental diff (the "changed A, broke B" case), the anchor is marked `base-side regression at <file:line>` — distinct from `unresolved` — so the highest-diagnostic-value regression signal is preserved rather than degrading to unresolved. This is the same fail-safe posture as `fail-safe-dispatch`'s three-state `UNKNOWN` (a lookup that cannot establish state is a fail-safe signal, never a silent success); D2 is the local-parse analogue. *Why over silent fallback:* a fabricated location is worse than an honest gap — `dev` chasing a wrong line is the exact failure this change exists to kill.

**D3 — Bundle threshold + related-file grouping; bundling on both paths, accounting revise-only.** Splitting triggers when the diff exceeds a configurable threshold (starting point: `>10` changed files or `>800` diff lines, tuned empirically). Grouping follows `open-code-review`'s smart-bundling rule — an implementation file travels with its test and collocated i18n/config — so isolated per-bundle context does not lose cross-file semantics. **Bundling itself fires on both green and revise paths** (it is orchestrator mechanics, color-independent); **per-criterion coverage accounting + the structured `criteria_coverage` field fire on the revise path only** (revise needs the audit; green stays quick-confirm). This resolves the R1 conflict between bundle coverage and the green-pass contract without deepening green-path review. *Alternative:* fixed-size chunking — rejected: it breaks related files across bundles and re-introduces the coverage hole. *Alternative:* bundling revise-only — rejected: it abandons the "large green diff also defends against skipped files" goal.

**D4 — Reflection kept lightweight, revise-path only.** Borrow `comment-reflection` as a single short self-check of the generated "how to fix" guidance, inside the same persona turn (no separate sub-agent). It fires only on the `revise` path; the `pass` path is not reflected on. *Alternative:* an independent reflection persona — rejected: cost and complexity out of proportion for a gate that already has the test gate as backstop.

**D5 — Philosophical fork, called out (revised).** `pa-verify`'s pass threshold stays backed by the test gate (high Recall). We deliberately do **not** adopt `open-code-review`'s lower-Recall / precision-over-noise stance. *Why the trade-off does not transfer:* `open-code-review` lowers Recall to reduce **comment noise** — a human reviewer triaging a stream of review comments benefits from fewer false alarms. `pa-verify`'s `revise` output is not a comment stream — it is a structured, high-signal construction directive to `dev`. The noise/precision trade-off that justifies low Recall for a human-facing comment tool does not migrate to a structured feedback gate, and the test gate backstop makes high Recall safe. (The earlier "lowering Recall would conflict with the test gate" framing was withdrawn in R1 response as a non-sequitur: the test gate and `pa-verify` are serial gates that do not share a decision.)

**D6 — `pa-verify`'s ADR anchor is the mechanical-vs-semantic split, not ADR-0005's text.** ADR-0005 (`report-mechanical-not-persona`) textually governs the **report** stage; `pa-verify` runs in the **dispatch** sub-loop (`run_daily.py` `_pa_verify_round`, SPEC §4.4). So ADR-0005 does not directly govern `pa-verify`, and `verify-commit-loop-design.md` §4's invocation of ADR-0005's "later add a pa-insight persona" clause is a borrowed analogy, not textual coverage. The real anchor is the **mechanical-vs-semantic split principle** that ADR-0005 itself instantiates for report (semantic work is what justifies a persona; mechanical stages stay deterministic) — applied pipeline-wide, `pa-verify` doing semantic judgement is exactly what merits a persona, and is the same kind of incremental persona the ADR-0005 clause anticipates. This change therefore does not touch dispatch/report's mechanical nature; it tightens an existing semantic persona's contract. Consequent doc debt: SPEC §4.5 still says "3 语义 persona" (radar/prd/prd-critic) — `pa-verify` is already the 4th; task 6.3 writes the count back.

## Risks / Trade-offs

- **Anchor mapping depends on test-output format** → different runners emit different failure formats. *Mitigation:* ship mappers for `jest` and `pytest` first (declared as first-supported runners in the spec); unknown formats hit D2's fail-open path; the dispatcher selects the mapper by the discovered `test_cmd` type.
- **Bundle threshold is hard to tune** → too low = overhead, too high = reverts to single-pass. *Mitigation:* threshold is config-driven, starts conservative, tuned against real dispatch records; small diffs stay single-pass.
- **Reflection adds tokens/latency to the revise path** → *Mitigation:* revise-only trigger and a short self-check prompt; pass path untouched.
- **Anchor structure vs the existing text `feedback_section` contract** → `dev` consumes `feedback_section` as markdown today (`dev-agent.py` does not parse it — grep-confirmed). *Mitigation:* anchors are **embedded alongside** the existing markdown location text as a structured sub-field; the markdown section `dev` reads stays backward-compatible; no target-plane change (ADR-0002).
- **`criteria_coverage` is persona-produced on the revise path** → it is a structured field but its content correctness still depends on persona judgement, which is deferred to the golden-suite follow-up. *Mitigation:* the field's *presence and shape* are machine-asserted (scenario contract); its *correctness* is design guidance until the golden suite lands.

## Migration Plan

- Control-plane-only increment; no data migration, no target-plane change.
- Ship behind a **new flag in the existing `LoopFlags` pattern** (precedent: `cross_prd_learning_shadow` / `cross_prd_learning_injection` — each new flag came with an env-var name and a domain-cut decision). Adding the flag touches `scripts/feature_flags.py` (`LoopFlags` + `FLAGS_ENV_MAP`, which is a stable external contract — see proposal Impact) and is task 4.0.
- **Rollout = flag-gated + manual verdict audit, not shadow-parity cutover.** `runtime-cutover-evidence`'s shadow-parity semantics ("decision unchanged with/without the flag") do not fit a change that alters `pa-verify`'s *input shape*: with the flag shadow-off, the verdict is trivially unchanged (parity passes vacuously); with it on, the verdict is *expected* to change (that is the point) — so parity would block the cutover it is supposed to gate. Instead: ship flag-off by default; enable per-project; audit N real dispatch records (compare revise feedback actionability with/without anchors+bundles) before broadening. Rollback = flag off; behavior reverts to the current single-pass, free-form-feedback path.

## Open Questions

- Exact starting values for the bundle threshold (file count, diff lines) — resolve against real dispatch records during implementation.
- Whether reflection should fire on every `revise` or only when the location anchor is `unresolved`/`base-side` — start revise-always (resolves OQ-Q2), revisit if cost is unjustified.
- Coverage of non-standard `test_cmd` shapes (custom scripts discovered from `package.json`) — accept fail-open initially, expand mappers as real cases appear.

## References

- `alibaba/open-code-review` (Apache-2.0) — README at `main`, commit `0f3c920fc8ff091cbd1ad9e0632bf54bba56e46b` (2026-08-03), retrieved and verified line-by-line 2026-08-04. URL: https://github.com/alibaba/open-code-review/blob/main/README.md . Quoted: `comment-positioning` + `comment-reflection` modules; `smart file bundling` ("Groups related files into a single review unit... each bundle runs as a sub-agent with isolated context — divide-and-conquer... naturally supports concurrent review"); root-cause quote ("a purely language-driven architecture lacks hard constraints on the review process").
- `alibaba/skill-up` (Apache-2.0) — README at `main`, commit `295378249e807b174938219d38df996a7faa9651` (2026-08-03). Referenced for the meta-layer follow-up (persona golden-suite), not directly used by this change.
