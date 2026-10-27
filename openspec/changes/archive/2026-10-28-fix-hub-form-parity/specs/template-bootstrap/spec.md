## ADDED Requirements

### Requirement: 样例覆盖面（profile.example.yaml 双形态教学）
`bootstrap/samples/profile.example.yaml` SHALL 同时教学两种项目接入形态：①本机 clone 形态（`repo: /abs/path/...` 绝对路径，pa 在本机 clone 内派发）②forge 形态（`forge: gitlab|gitcode` + `gitlab_host`（gitlab 带 scheme 可带端口）/ 对应 forge host 键 + `repo: 组/子组/仓 全路径字符串`）。样例 MUST 显式对照注明两种形态下 `repo` 键的不同语义（本地 clone 绝对路径 vs forge 全路径字符串），防混。样例 SHALL 为注释教学形态（可整块复制后按需删减），MUST NOT 改变 profile 解析器任何行为。

#### Scenario: forge 用户有样例可抄
- **WHEN** 用户要接入一个自建 GitLab 仓（组/子组/仓 多级路径）并查阅 profile.example.yaml
- **THEN** 样例含 forge 形态三键（`forge` / `gitlab_host` / `repo` 全路径）的注释段，照抄填值即可产出合法 profile

#### Scenario: 两种 repo 语义显式区分
- **WHEN** 样例同时展示本机 clone 形态与 forge 形态
- **THEN** 注释显式对照 `repo` 在两种形态下的不同语义（本地 clone 绝对路径 vs forge 全路径字符串），不含混表述
