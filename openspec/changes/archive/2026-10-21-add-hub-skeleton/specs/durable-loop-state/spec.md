## ADDED Requirements

### Requirement: Run directory naming contract
The run directory naming format `<stamp>_<slug>` under `state/runs/<project>/` SHALL be part of the journal contract, not an incidental implementation detail: external indexers (the execution hub DB and its on-volume `.meta.json` signing) rely on this format to align runs with their audit metadata. `<stamp>` SHALL be an 8-digit date (`YYYYMMDD`) with an optional collision-avoidance suffix (`m`, `m2`, `m3`, ... — the `_bump_stamp_suffix` form). Changing the format MUST go through change review — it is no longer a free-form internal convention.

#### Scenario: Stamp collision bump is conditional
- **WHEN** the inject stage runs with a `--stamp` for which no `prd_manifest_<stamp>.json` exists in the state directory (the global stamp namespace, across projects; under the execution-hub deployment this namespace becomes per-project volume-local at host `<data_root>/<project>/state` — the in-container pa perspective and this conditional-bump contract are unchanged, ontological audit B3)
- **THEN** the stamp is used as passed (no bump); a bump suffix is appended ONLY while `prd_manifest_<stamp>.json` exists — the execution hub relies on this conditionality when it pre-allocates a fresh stamp (scanning existing manifests) and passes it via `--stamp`, so that meta file, `--stamp`, and actual run directory stay aligned

#### Scenario: Hub aligns meta signing to run directory
- **WHEN** the execution hub writes its signature meta file before starting a container
- **THEN** it derives `<stamp>_<slug>` from the PRD artifact naming and places `<stamp>_<slug>.meta.json` in the same run directory shape, expecting pa to keep the format stable

#### Scenario: Format change requires review
- **WHEN** a future change proposes renaming or restructuring run directories (e.g. adding fields, changing the stamp format)
- **THEN** the change must explicitly address the impact on external indexers (hub DB, meta files, recovery tooling) rather than landing as an internal refactor
