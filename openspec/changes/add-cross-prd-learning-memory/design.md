## Context

The control plane already records SDK session metadata, per-turn run logs, content-addressed test and verifier artifacts, and journal-derived terminal states. Retry context can consume verifier feedback, but a later, unrelated PRD starts without reusable knowledge from prior runs. The requested capability is therefore not a larger final summary; it is a controlled promotion loop that turns evidence from multiple terminal PRDs into bounded project memory.

The design must preserve the repository's existing boundaries: PRDs remain immutable, control-plane state never enters target repositories, SDK success is not equivalent to verification, and optional observability must not rewrite business outcomes. The pinned Agent SDK and model-routing convention remain unchanged.

## Goals / Non-Goals

**Goals:**

- Extract reusable candidate lessons after the full PRD outcome, including independent verification, is known.
- Require evidence and cross-PRD recurrence before ordinary lessons become active.
- Inject only a small relevant subset into future PRD development prompts.
- Measure whether injected lessons were followed and useful.
- Make the catalog reconstructable and incorrect lessons reversible.
- Preserve all existing test, verification, retry, and publication semantics.

**Non-Goals:**

- Treating an end-of-session narrative as trusted long-term memory.
- Feeding lessons back into another iteration of the same PRD as the primary use case.
- Writing memory into a target repository, `CLAUDE.md`, a pull request, or the immutable PRD.
- Cross-project/global lesson promotion in V1.
- Vector search, an external database, an embedding service, or a new model dependency.
- Allowing reflection failure to fail an otherwise valid delivery.

## Decisions

### 1. Reflect after PRD terminal evidence, not in the Stop hook

`Stop` is an inner lifecycle signal and may fire repeatedly while the fresh-test continuation gate is active. The useful learning boundary is after dispatch has a terminal record containing the SDK result, test evidence, verifier verdict, retry exhaustion if any, and reconciliation result.

After the terminal event is durably recorded, a side-channel learning step builds a curated evidence envelope and invokes reflection. This step cannot alter the already-recorded terminal outcome.

Alternative considered: add reflection instructions to the primary dev prompt. Rejected because the output precedes independent verification, is not structurally guaranteed, and conflates delivery summary with durable learning.

### 2. Use a separate read-only SDK synthesis call

The learning step uses the existing SDK with model omitted, no mutable tools, a bounded turn/time budget, and a strict JSON response schema. It receives only sanitized metadata and integrity-checked artifact excerpts needed for diagnosis. It does not resume or overwrite the primary development session metadata.

A separate call is preferred over resuming the original session because the final verifier evidence was produced outside that session, and because resuming could confuse retry ownership or permit context-specific claims to masquerade as evidence. Candidate generation remains semantic; schema validation, evidence verification, and promotion remain mechanical.

Alternative considered: deterministic extraction only. Rejected because normalizing semantically equivalent mistakes and expressing useful applicability boundaries requires semantic judgment. Deterministic checks still own every trust boundary.

### 3. Store facts append-only and derive the catalog

V1 state layout:

```text
.project-auto/state/lessons/
  candidates/<project>.jsonl
  events/<project>.jsonl
  catalog/<project>.json
```

Candidate and lifecycle event records are append-only, versioned, and correlated with `run_id`, `prd_id`, and terminal journal evidence. The catalog is an atomic, rebuildable projection and is never the sole source of truth. Full reflection output is stored as a new content-addressed `reflection` artifact; logs contain references and safe structured fields rather than raw transcripts.

Alternative considered: update one Markdown memory file in place. Rejected because concurrent dispatches would lose updates, provenance would be weak, and retirement or correction would destroy history.

### 4. Separate semantic normalization from mechanical promotion

The reflection output proposes a normalized `pattern_key`, applicability tags, boundaries, corrective action, and evidence references. Mechanical validation rejects missing evidence, invalid schema, task-local summaries, unsafe text, and unknown policy values.

Ordinary promotion requires supporting candidates from at least two distinct PRD IDs in the same project. Repeated iterations of one PRD count once. A one-occurrence fast path exists only for a code-owned allowlist of critical invariant classes and requires matching independent verifier evidence; an LLM-provided severity cannot activate it.

Merges preserve all source candidate IDs and evidence lineages. Conflicting corrective actions create a conflict event and remain inactive until later evidence resolves them.

Alternative considered: promote every high-confidence model response. Rejected because confidence is self-reported and would turn model mistakes into persistent prompt poisoning.

### 5. Retrieve with bounded deterministic metadata matching

At dispatch entry, the control plane derives task metadata from the project profile and PRD: project ID, language or surface hints, acceptance categories, lifecycle stage, and declared paths when present. Active lessons are filtered by `applies_when`/`does_not_apply_when`, then ranked by applicability overlap, verified support count, effectiveness history, confidence, and recency.

At most five lessons are rendered as concise trigger/action/boundary checklist entries. Evidence bodies and historical narratives are not injected. The prompt records injected lesson IDs so terminal processing can evaluate their outcomes.

Alternative considered: inject the entire project catalog. Rejected because it wastes context, increases instruction conflicts, and makes applicability impossible to audit. Embedding search is deferred until deterministic retrieval has measurable recall problems.

### 6. Close the loop with evidence-derived usage outcomes

The terminal learning step evaluates every injected lesson. It records whether evidence shows the prescribed action occurred and whether the associated failure recurred. Semantic synthesis may explain ambiguous results, but deterministic state transitions apply bounded confidence updates and decide active/superseded/retired status.

Absence of a detectable action is recorded as `unknown`, not automatically as disobedience. A contradicted lesson loses confidence; repeated contradiction retires it. Retirement only changes the projection and never deletes source facts.

### 7. Fail open for delivery, fail closed for memory

Reflection timeout, invalid JSON, corrupt candidate history, or catalog write failure cannot change the underlying PRD result. The system emits a structured `learning_memory_degraded` record and continues reporting the real delivery outcome.

The same failures do block candidate promotion and lesson injection. A malformed catalog is skipped rather than partially trusted. This gives memory lower authority than tests and verifier gates while preventing corrupt advice from entering a later prompt.

### 8. Roll out behind one disabled-by-default flag

A single coordinator-resolved `cross_prd_learning` flag controls candidate generation, projection, retrieval, and injection as one coherent capability. Disabled mode performs no SDK reflection call and preserves the current prompt and state behavior. Shadow mode may generate and project candidates without injecting lessons; enabled mode adds bounded injection after parity and quality evidence pass.

Using one capability flag avoids partial states where lessons are injected without provenance or candidates accumulate without lifecycle accounting.

## Risks / Trade-offs

- [Semantic normalization merges unrelated failures] -> Require applicability boundaries, retain source lineages, and keep conflicting actions inactive.
- [Persistent lesson poisons future prompts] -> Require cross-PRD evidence, cap injection at five, track contradictions, and support deterministic retirement.
- [Reflection adds SDK cost and latency] -> Run once per terminal PRD with strict bounds; do not run per Stop or per retry iteration.
- [Concurrent project dispatches append and rebuild simultaneously] -> Use append locking plus atomic catalog replacement; per-project facts remain replayable after a crash.
- [Evidence contains secrets or excessive transcripts] -> Use sanitized content-addressed artifacts, safe excerpts, schema length limits, and no raw evidence in prompts or catalogs.
- [A memory outage becomes confused with delivery failure] -> Keep separate status fields and ensure existing terminal predicates never consume memory status.
- [Deterministic retrieval misses semantically related lessons] -> Record retrieval outcomes first; consider embeddings only after measured misses justify a new design.
- [Critical fast path grows into a bypass] -> Keep invariant classes code-owned and narrow; require independent verifier evidence and explicit tests per class.

## Migration Plan

1. Add schemas, append-only stores, projection, and replay tests with the feature disabled.
2. Add terminal evidence-envelope construction and read-only reflection in shadow mode; measure schema rejection, cost, and latency without prompt injection.
3. Backfill nothing automatically. Build candidates only from new terminal PRDs so every fact uses the current schema and evidence contract.
4. Enable project-level catalog projection and verify deterministic rebuild under crash/concurrency tests.
5. Enable retrieval in report-only mode and compare selected lessons with manual review.
6. Enable bounded prompt injection for one allowlisted project, then expand after no regression in existing quality and publication outcomes.
7. Roll back by disabling `cross_prd_learning`; existing candidate facts remain inert and no target repository cleanup is required.

## Open Questions

- Which mechanically allowlisted critical invariant classes are justified for the one-occurrence promotion exception in V1?
- Which existing PRD/profile fields are reliable enough for initial applicability tags without adding a new semantic classification call at dispatch time?
- What bounded timeout and cost ceiling should the terminal reflection call use after measuring the current proxy's real usage reporting?
