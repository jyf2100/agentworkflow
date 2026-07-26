## 1. Contracts and Feature Boundary

- [ ] 1.1 Add versioned dataclasses/enums for lesson candidates, lesson lifecycle events, active catalog entries, applicability boundaries, evidence lineage, and usage outcomes, with round-trip and invalid-schema tests.
- [ ] 1.2 Add `reflection` to the content-addressed artifact kind allowlist and test sanitized storage, digest verification, and corrupt-artifact rejection.
- [ ] 1.3 Add one disabled-by-default coordinator-resolved `cross_prd_learning` feature flag with baseline, shadow, and enabled behavior tests; document that disabled mode performs no reflection call or prompt change.
- [ ] 1.4 Define the code-owned V1 critical-invariant allowlist and tests proving model-provided severity or an unknown invariant cannot activate single-occurrence promotion.

## 2. Append-Only Memory State

- [ ] 2.1 Implement per-project append-only candidate and lifecycle event writers under `.project-auto/state/lessons/` with versioning, file locking, fsync/atomicity behavior, and concurrent append tests.
- [ ] 2.2 Implement strict candidate validation for reusable trigger, executable corrective action, applicability and non-applicability boundaries, project/PRD/iteration identity, bounded field sizes, and integrity-checked evidence references.
- [ ] 2.3 Implement deterministic normalization and equivalence keys without allowing raw model output to choose storage paths, project scope, promotion count, or critical-invariant class.
- [ ] 2.4 Implement catalog projection and atomic replacement from append-only facts, including deterministic replay, duplicate-event idempotency, malformed-middle-record fail-closed behavior, and incomplete-trailing-record recovery.
- [ ] 2.5 Add tests proving all memory state remains in ignored control-plane paths and never appears in target worktrees, commits, pull requests, or immutable PRDs.

## 3. Cross-PRD Promotion

- [ ] 3.1 Implement ordinary promotion only after equivalent valid candidates reference at least two distinct PRD IDs in the same project; test that repeated iterations of one PRD count once.
- [ ] 3.2 Implement merge behavior that preserves every source candidate and evidence lineage while keeping conflicting corrective actions inactive and auditable.
- [ ] 3.3 Implement the verifier-confirmed critical-invariant fast path and counterexamples for semantic-only severity, missing verifier evidence, and non-allowlisted invariant classes.
- [ ] 3.4 Implement active, conflicted, superseded, and retired catalog states as projections that never delete source facts.

## 4. Terminal Reflection

- [ ] 4.1 Build a curated terminal evidence envelope from recorded PRD outcome, SDK/session metadata, run-log references, test evidence, verifier verdict, retry/reconciliation status, and injected lesson IDs, excluding raw secrets and unbounded transcripts.
- [ ] 4.2 Implement a separate bounded read-only Agent SDK reflection call with model omitted, no mutable tools, strict JSON parsing, timeout handling, and no writes to primary development session metadata.
- [ ] 4.3 Invoke reflection only after the PRD terminal event is durably recorded; prove repeated Stop hooks and intermediate retry iterations cannot generate or promote cross-PRD lessons.
- [ ] 4.4 Persist valid full reflection output as a sanitized content-addressed artifact and append accepted candidates with evidence references.
- [ ] 4.5 Record `learning_memory_degraded` on timeout, SDK error, invalid JSON, schema rejection, or persistence failure without changing test, verify, retry, publication, or terminal outcomes.

## 5. Retrieval and Prompt Injection

- [ ] 5.1 Derive deterministic retrieval metadata from the current project profile and immutable PRD without an additional semantic classification call.
- [ ] 5.2 Implement project-local filtering and ranking using applicability boundaries, verified support count, effectiveness history, confidence, and recency, with stable tie-breaking.
- [ ] 5.3 Enforce a maximum of five injected lessons and render only lesson ID, trigger, corrective action, and non-applicability boundary; exclude evidence bodies and historical narratives.
- [ ] 5.4 Inject the selected lesson block into the standard dev prompt and record selected lesson IDs for terminal effectiveness evaluation.
- [ ] 5.5 Add counterexamples proving unrelated, conflicted, superseded, retired, cross-project, malformed, or unreconciled lessons are not injected and cause truthful degraded-memory reporting where applicable.

## 6. Effectiveness and Lesson Lifecycle

- [ ] 6.1 Evaluate each injected lesson against terminal test, journal, and verifier evidence, distinguishing followed, not observed, recurrence prevented, recurrence observed, contradicted, and unknown outcomes.
- [ ] 6.2 Append usage and outcome facts and implement bounded deterministic confidence updates without treating absent evidence as agent disobedience.
- [ ] 6.3 Implement contradiction-driven downgrade, supersession, and retirement rules, with tests proving retired lessons remain replayable but are excluded from retrieval.
- [ ] 6.4 Extend dispatch/report records with memory mode, selected lesson IDs, candidate counts, promotions, and degraded status while keeping existing success/failure semantics unchanged.

## 7. Rollout, Recovery, and Verification

- [ ] 7.1 Add shadow mode that performs candidate extraction and catalog projection but never changes development prompts; produce parity evidence for existing dispatch terminal outcomes.
- [ ] 7.2 Add crash-recovery tests for failure before and after candidate append and before and after catalog replacement, proving idempotent replay and no duplicate promotion.
- [ ] 7.3 Add one allowlisted-project canary covering two distinct PRDs with an equivalent evidence-backed pattern, promotion after the second PRD, relevant injection into a third PRD, and terminal effectiveness feedback.
- [ ] 7.4 Add negative canaries for a one-PRD repeated pattern, corrupt evidence, reflection outage, malformed catalog, irrelevant lesson, and contradicted lesson.
- [ ] 7.5 Update `CONTEXT.md`, `SPEC.md`, and `RUNBOOK.md` with the learning-memory boundary, state locations, degradation semantics, rebuild/disable procedure, and V1 project-only scope.
- [ ] 7.6 Run `bash scripts/quality.sh` from `Projects/项目推进流水线`, record total/passed/failed counts separately, and archive focused evidence that existing publication and retry predicates are unchanged.
