## 1. Contracts and Feature Boundary

- [x] 1.1 Add versioned dataclasses/enums for lesson candidates, lesson lifecycle events, active catalog entries, applicability boundaries, evidence lineage, and usage outcomes, with round-trip and invalid-schema tests.
- [x] 1.2 Add `reflection` to the content-addressed artifact kind allowlist and test sanitized storage, digest verification, and corrupt-artifact rejection.
- [x] 1.3a Add a disabled-by-default coordinator-resolved `cross_prd_learning_shadow` feature flag (env `PA_LEARNING_SHADOW`, profile.loop, default False) that, when on, runs read-only reflection and projects the catalog; when off performs no reflection call and no prompt or state change. Add baseline and shadow behavior tests.
- [x] 1.3b Add a disabled-by-default coordinator-resolved `cross_prd_learning_injection` feature flag (env `PA_LEARNING_INJECTION`, profile.loop, default False), gated on `cross_prd_learning_shadow=on` plus parity and quality evidence; add tests that the invalid `injection=on, shadow=off` combination degrades to shadow-only and emits `learning_memory_degraded` of class `injection_not_gated`.
- [x] 1.4 Add tests proving a model-authored `pattern_key`, `equivalence_key`, `invariant_class`, `project_id`, promotion count, or storage path is redacted at the schema boundary and cannot influence equivalence or promotion.

## 2. Append-Only Memory State

- [x] 2.1 Implement per-project append-only candidate and lifecycle event writers under `.project-auto/state/lessons/` with versioning, file locking, fsync/atomicity behavior, and concurrent append tests.
- [x] 2.2 Implement strict candidate validation for reusable trigger, executable corrective action, applicability and non-applicability boundaries, project/PRD/iteration identity, bounded field sizes, and integrity-checked evidence references.
- [x] 2.3 Implement a deterministic `equivalence_key = project_id + ':' + sha256(json.dumps((canonical(phase), canonical(failure_class), canonical(corrective_action_class), applicability_signature), separators=(',',':'), sort_keys=True))[:16]` where `canonical(t) = lower(t).replace('-','_').strip()` and `applicability_signature = sorted(set(canonical(t) for t in applies_when_tags)) or '__unscoped__'`; equivalence is byte-equal `equivalence_key`; reject out-of-vocabulary enum values and redact any model-authored `pattern_key`/`equivalence_key`/`invariant_class`/`project_id`/promotion-count/storage-path fields at the schema boundary.
- [x] 2.4 Implement catalog projection and atomic replacement from append-only facts, including deterministic replay, duplicate-event idempotency, malformed-middle-record fail-closed behavior, and incomplete-trailing-record recovery.
- [x] 2.5 Add tests proving all memory state remains in ignored control-plane paths and never appears in target worktrees, commits, pull requests, or immutable PRDs.

## 3. Cross-PRD Promotion

- [x] 3.1 Implement ordinary promotion only after equivalent valid candidates reference at least two distinct PRD IDs in the same project; test that repeated iterations of one PRD count once.
- [x] 3.2 Implement merge behavior that preserves every source candidate and evidence lineage while keeping conflicting corrective actions inactive and auditable.
- [x] 3.3 Add counterexamples proving single-occurrence candidates are never promoted in V1 — including a verifier-confirmed critical invariant violation from one PRD, a model self-labeled critical, and any `unknown` enum value — even when cross-PRD recurrence would otherwise be the only missing condition.
- [x] 3.4 Implement active, conflicted, superseded, and retired catalog states as projections that never delete source facts.

## 4. Terminal Reflection

- [x] 4.1 Build a curated terminal evidence envelope selected by the journal's actual verifier transition history, not the terminal status label: reference verifier verdict (or revise sequence + exhaustion record) when the journal contains any verifier event, including post-verifier short-circuits such as publication reconcile UNKNOWN after a verifier pass; reference matching mechanical gate / stall / SDK-session / external-state evidence only when the journal records no verifier event (pre-verifier short-circuit). Add counterexamples for pre-verifier `blocked_external_state`, post-verifier `blocked_external_state`, `verifier-revise-exhausted`, and pre/post-verifier `blocked_evidence`. Exclude raw secrets and unbounded transcripts.
- [x] 4.2 Implement a separate bounded read-only Agent SDK reflection call with model omitted, no mutable tools, strict JSON parsing, timeout handling, and no writes to primary development session metadata.
- [x] 4.3 Invoke reflection only after the PRD terminal event is durably recorded; prove repeated Stop hooks and intermediate retry iterations cannot generate or promote cross-PRD lessons.
- [x] 4.4 Persist valid full reflection output as a sanitized content-addressed artifact and append accepted candidates with evidence references.
- [x] 4.5 Record `learning_memory_degraded` on timeout, SDK error, invalid JSON, schema rejection, persistence failure, or evidence history mismatch with the journal's verifier transition (e.g., a pre-verifier short-circuit candidate citing a verifier verdict, or a post-verifier terminal lacking verifier evidence), without changing test, verify, retry, publication, or terminal outcomes.

## 5. Retrieval and Prompt Injection

- [x] 5.1 Derive deterministic retrieval metadata from the current project profile and immutable PRD without an additional semantic classification call.
- [x] 5.2 Implement project-local filtering and ranking using applicability boundaries, verified support count, effectiveness history, confidence, and recency, with stable tie-breaking.
- [x] 5.3 Enforce a maximum of five injected lessons and render only lesson ID, trigger, corrective action, and non-applicability boundary; exclude evidence bodies and historical narratives.
- [x] 5.4 Inject the selected lesson block into the standard dev prompt and record selected lesson IDs for terminal effectiveness evaluation.
- [x] 5.5 Add counterexamples proving unrelated, conflicted, superseded, retired, cross-project, malformed, or unreconciled lessons are not injected and cause truthful degraded-memory reporting where applicable.

## 6. Effectiveness and Lesson Lifecycle

- [x] 6.1 Evaluate each injected lesson against terminal test, journal, and verifier evidence, distinguishing followed, not observed, recurrence prevented, recurrence observed, contradicted, and unknown outcomes.
- [x] 6.2 Append usage and outcome facts and implement bounded deterministic confidence updates without treating absent evidence as agent disobedience.
- [x] 6.3 Implement contradiction-driven downgrade, supersession, and retirement rules, with tests proving retired lessons remain replayable but are excluded from retrieval.
- [x] 6.4 Extend dispatch/report records with memory mode as a `(shadow_on, injection_on)` boolean pair, selected lesson IDs, candidate counts, promotions, and degraded status while keeping existing success/failure semantics unchanged.

## 7. Rollout, Recovery, and Verification

- [x] 7.1 Add shadow mode that performs candidate extraction and catalog projection but never changes development prompts; produce parity evidence for existing dispatch terminal outcomes.
- [x] 7.2 Add crash-recovery tests for failure before and after candidate append and before and after catalog replacement, proving idempotent replay and no duplicate promotion.
- [x] 7.3 Add one allowlisted-project canary covering two distinct PRDs with an equivalent evidence-backed pattern, promotion after the second PRD, relevant injection into a third PRD, and terminal effectiveness feedback.
- [x] 7.4 Add negative canaries for a one-PRD repeated pattern, corrupt evidence, reflection outage, malformed catalog, irrelevant lesson, and contradicted lesson.
- [x] 7.5 Update `CONTEXT.md`, `SPEC.md`, and `RUNBOOK.md` with the learning-memory boundary, state locations, degradation semantics, rebuild/disable procedure, and V1 project-only scope.
- [x] 7.6 Run `bash scripts/quality.sh` from `Projects/项目推进流水线`, record total/passed/failed counts separately, and archive focused evidence that existing publication and retry predicates are unchanged.
- [x] 7.7 Document and test two-level rollback: disabling `cross_prd_learning_injection` keeps shadow candidate generation running with no prompt change; disabling `cross_prd_learning_shadow` stops reflection entirely while existing candidate facts remain inert and rebuildable.
