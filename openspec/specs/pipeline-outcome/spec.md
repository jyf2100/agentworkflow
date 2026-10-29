# pipeline-outcome

## Purpose

业务终态结构化出口——「容器跑成功」与「干成绿活」的本体裂缝出口：run 末产单一机器可读 outcome 文件（六桶业务终态 + PR 身份 + 附录 exit code 非判据），stdout 全程留痕，hub 通知消费业务终态而非裸 exit code，dispatch record PR 身份对称。消歧：报告/exit code/metrics 均 _Avoid_（见 CONTEXT.md 术语表）。


## Requirements

### Requirement: Business-outcome structured export

The orchestrator SHALL write a single machine-readable pipeline outcome file (`pipeline_outcome_<stamp>.json`, schema `pa.pipeline_outcome.v1`) at the end of the report stage, covering run identity, per-PRD terminal states (status, three-state verify verdict, PR identity, one-line reason headline), and aggregate counts. The file is the authoritative business-outcome export; the container exit code is recorded in it as an appendix field and MUST NOT be the business verdict for any consumer.

#### Scenario: Red run exports red, not exit-code green

- **WHEN** a dispatch completes with verify verdict red and the PR left for review while the run container exits zero
- **THEN** the outcome file records that PRD with verify verdict `red`, its PR identity, and a reason headline; the summary counts red ≥ 1 regardless of container exit code

#### Scenario: Outcome write failure does not block the run

- **WHEN** the outcome file cannot be written (disk error, permission)
- **THEN** the orchestrator warns and completes the report stage (observability is fail-open); notification consumers fall back to the exit-code path

#### Scenario: Both orchestrator entrypoints produce the outcome

- **WHEN** a run completes via the legacy `run_daily.py` path or the graph orchestrator path
- **THEN** both write the outcome through the same report-stage exit function — no third hand-assembly site

### Requirement: Orchestrator stdout persisted per run

The orchestrator SHALL tee its stage/label progress lines and captured command output to a per-run `.stdout.log` under the run's state directory, so the full orchestration trace remains host-reviewable after the run container is destroyed. LLM session transcripts are excluded (they live in their own stream files).

#### Scenario: Stdout survives container destruction

- **WHEN** a run container completes and is removed by the hub
- **THEN** the run's state directory still contains the complete `.stdout.log` written so far, including the report-stage classification line

#### Scenario: Tee failure degrades to terminal-only

- **WHEN** the tee target becomes unwritable mid-run
- **THEN** the orchestrator continues (warn logged); stdout-to-terminal behavior is unaffected — observability never blocks orchestration

### Requirement: Run completion notification carries business outcome

The hub's run-completion notification SHALL, when the pipeline outcome file exists, lead with the business summary (verdict counts, PR links with red/green markers, reason headline of the first red) and demote the container exit code to a trailing field. When the outcome file is absent, the notification falls back to the current exit-code form and the fallback is logged hub-side.

#### Scenario: Notification reports red on an exit-zero run

- **WHEN** a run exits zero but its outcome file records one red PRD left for review
- **THEN** the notification text leads with the red count and the PR's failing marker and reason headline — an operator reading the notification cannot mistake the run for all-green

#### Scenario: Legacy run without outcome file falls back

- **WHEN** a run produced no pipeline outcome file (older image, or outcome write failed)
- **THEN** the notification falls back to the exit-code form and the fallback occurrence is logged — the hub never guesses a business verdict from artifacts it does not own

### Requirement: PR identity fields are written symmetrically

Every dispatch-record write that carries a pull-request identity SHALL populate both `pr_url` and `pr_number` from the same PR-creation/lookup receipt; one populated with the other absent is a defect, not an acceptable state.

#### Scenario: PR opened populates both fields

- **WHEN** a PR is created or looked up and its identity flows into the dispatch record
- **THEN** both `pr_url` and `pr_number` are recorded from that receipt in the same write

### Requirement: Verify report carries per-command output pointers

The structured verify report SHALL give each declared command's result an explicit output reference (per-command output file), while the existing single `test_log` field is preserved as the line-anchor parsing input. A failing command's real output MUST be reachable without reading the raw session stream.

#### Scenario: Red command output is reachable from the report

- **WHEN** a declared command exits non-zero and the report records it
- **THEN** the report carries an output reference for that command whose content contains the command's stderr/stdout tail — diagnosing the red does not require the 10k-line raw stream
