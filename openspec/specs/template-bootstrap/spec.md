# template-bootstrap

## Purpose

Define how a fresh clone of the template becomes a working pipeline instance: `bootstrap.sh` initializes instance-local data in clean directories (config, instance state), detects conflicting pre-existing environments instead of overwriting them, warns when projected files carry local modifications (divergence from upstream that shifts upgrade responsibility to the instance owner), and keeps optional capabilities disabled by default.

## Requirements
### Requirement: 干净目录初始化
bootstrap.sh SHALL 在 clone 后的 template 目录内完成实例化：创建 `config/`（sources 空模板、通知开关样例）、安装依赖（`pip install -e ".[dev]"`）、执行 claude CLI 前置检查（`claude --version` 与端点连通冒烟）。任一前置失败 MUST 以明确指引退出非零，MUST NOT 静默继续。

#### Scenario: 正常初始化
- **WHEN** 在具备 Python ≥3.11 与可用 claude CLI 的机器上运行 bootstrap.sh
- **THEN** config 目录建立、依赖安装成功、前置冒烟通过，输出下一步上手指引

#### Scenario: claude CLI 不可用
- **WHEN** claude CLI 缺失或端点连通冒烟失败
- **THEN** bootstrap 以非零退出并输出「需要可用 claude CLI」的指引，不继续后续步骤
### Requirement: 已有环境冲突检测
bootstrap 检测到同事机已有 `.claude/` 环境与 template persona/settings 的冲突时（同名 persona 文件、settings hooks/env 交互点）SHALL 逐项列出冲突并默认拒绝覆盖；MUST NOT 静默合并。

#### Scenario: 同名 persona 冲突
- **WHEN** 同事机 `~/.claude/agents/` 已存在与 template persona 同名的文件
- **THEN** bootstrap 列出冲突文件并停在该步骤等待显式确认，默认不覆盖
### Requirement: 本地改动检测（防分叉）
bootstrap 重跑（升级路径 `git pull` + bootstrap）SHALL 将同事仓 `scripts/`、`.claude/agents/`、`openspec/` 与 template tag 基线 diff；检出漂移 MUST 警告并输出 diff 摘要，MUST NOT 静默覆盖本地改动。`config/`、`profiles/`、`.project-auto/` 的本地修改为合法实例数据，不在检测范围。

#### Scenario: 篡改 scripts 被检出
- **WHEN** 同事仓 `scripts/` 下文件与 template tag 基线不一致
- **THEN** bootstrap 输出漂移警告与 diff 摘要，并指引回流路径（上报 vault），不覆盖该文件

#### Scenario: 干净实例正常升级
- **WHEN** 同事仓受检路径与 tag 基线一致
- **THEN** 升级流程正常完成且不产生漂移警告
### Requirement: 可选项缺省关闭
cron 安装与 SMTP 通知 SHALL 为可选项且缺省关闭；不选择即不安装 cron、通知止于报告落盘。

#### Scenario: 跳过 cron
- **WHEN** bootstrap 交互中同事跳过可选 cron 安装
- **THEN** 不写入任何 crontab 条目，流水线仍可手动触发
### Requirement: 样例覆盖面（profile.example.yaml 双形态教学）
`bootstrap/samples/profile.example.yaml` SHALL 同时教学两种项目接入形态：①本机 clone 形态（`repo: /abs/path/...` 绝对路径，pa 在本机 clone 内派发）②forge 形态（`forge: gitlab|gitcode` + `gitlab_host`（gitlab 带 scheme 可带端口）/ 对应 forge host 键 + `repo: 组/子组/仓 全路径字符串`）。样例 MUST 显式对照注明两种形态下 `repo` 键的不同语义（本地 clone 绝对路径 vs forge 全路径字符串），防混。样例 SHALL 为注释教学形态（可整块复制后按需删减），MUST NOT 改变 profile 解析器任何行为。

#### Scenario: forge 用户有样例可抄
- **WHEN** 用户要接入一个自建 GitLab 仓（组/子组/仓 多级路径）并查阅 profile.example.yaml
- **THEN** 样例含 forge 形态三键（`forge` / `gitlab_host` / `repo` 全路径）的注释段，照抄填值即可产出合法 profile

#### Scenario: 两种 repo 语义显式区分
- **WHEN** 样例同时展示本机 clone 形态与 forge 形态
- **THEN** 注释显式对照 `repo` 在两种形态下的不同语义（本地 clone 绝对路径 vs forge 全路径字符串），不含混表述
