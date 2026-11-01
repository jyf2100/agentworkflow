## ADDED Requirements

### Requirement: sources 样例教学形态（github-repo 完整示例钉定）

`bootstrap/samples/sources.yaml` SHALL 保持空模板活段（`sources: []`——bootstrap 装出的实例起点不预置任何活源），并以注释教学形态收录至少一个字段全集的 `kind: github-repo` 示例条目（`name` / `kind` / `root` / `content_glob` / `params` 嵌套的 `repos`+`window` / `target_projects` / `marker`）。kind 专属参数 MUST 以 `params` 嵌套呈现（对齐 fetch prompt 消费面只读 `params.*`），MUST NOT 以平铺键示例（照抄平铺键的源 fetch 会静默空转）。示例条目 MUST 注明 `target_projects` 缺省不喂任何项目的语义与 `content_glob`/`marker` 的 radar 消费面必填地位。样例 MUST NOT 改变 sources 装载器任何行为。

#### Scenario: 新实例有 github-repo 源可照抄

- **WHEN** 同事要监控若干 GitHub 仓并查阅 bootstrap 装出的 sources.yaml
- **THEN** 文件含一个可整块取消注释即生效的 github-repo 完整条目（`params` 嵌套、`content_glob`/`marker`/`target_projects` 全集），照抄改 `repos` 即产出合法源

#### Scenario: 空模板语义不破

- **WHEN** bootstrap 把样例装成实例起点（`.project-auto/sources.yaml` 缺失时整份复制）
- **THEN** 活段仍为 `sources: []`——实例不因模板默认跟踪任何仓（跟踪谁是实例自己的运行时决定）

#### Scenario: params 嵌套与消费面一致

- **WHEN** 示例条目被整块活化（占位符替换为真实项目名）并经 `load_sources` 装载
- **THEN** `github_repo_prompt` 消费面读到 `params.repos` / `params.window`（教学形态与代码契约不漂移）
