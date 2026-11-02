# Design: add-helix-signal-source

## D1 活化形态：注释教学条目，live 段保持 `sources: []`

- **选**：示例条目以注释块落在 sample（整块取消注释即生效），活段 `sources: []` 不动。
- **否（活条目）**：`bootstrap.sh:59-66` 的 `install_sample` 把样例**整份复制**成实例起点（本机形态 `$TOP/.project-auto/sources.yaml`；hub 形态每项目 `HUB_DATA_ROOT/<name>/sources.yaml` 是启动必读面、面板建项亦落空模板）——sample 放活条目 = 每个新实例、每台同事机每日对一个与其项目无关的仓跑 fetch（persona LLM 调用 + gh API），而 radar 端 `target_projects` 缺省一个项目都不喂（`run_daily.py:1631-1636`）：纯成本零收益。「跟踪 didi/helixharness」是**本仓实例**的运行时决定，不是 template 的默认值。
- **教学形态先例**：template-bootstrap 既有「样例覆盖面」requirement 对 profile.example.yaml 的定性就是「注释教学形态（可整块复制后按需删减）」（`openspec/specs/template-bootstrap/spec.md:41`）；本 change 把同一形态契约以 ADDED requirement 扩到姊妹样例 sources.yaml。
- **已知边界（bootstrap 语义）**：`install_sample` 只建缺失、绝不覆盖已存在实例（`bootstrap.sh:61`）——已 bootstrap 过的机器不会自动拿到新样例，须人工同步；这正是 tasks 2.1 写「缺则从 bootstrap sample 复制起」的原因。
- **落点澄清**：「让 radar 用本职管道跟踪它」的完成面不在 sample（template 面）而在各机实例面（tasks 2.x，gitignore 实例数据：`.project-auto/` 见 `.gitignore:2`，内容根 `Knowledge/` 同 `.gitignore:5`）。

## D2 字段形态：`params` 嵌套 + ADR-0007 条目全集

- **消费面实证（嵌套非平铺）**：`github_repo_prompt` 只读 `params.repos` / `params.window`（`run_daily.py:1379-1381`）；`wechat_url_prompt` 读 `params.urls`（`:1367`）；`fetch_prompt` 读 `params.prompts`（`:1356`）。原样例结构注释把这三组键写平、且深研键名误作 `topic`——照抄出来的源 fetch prompt 拿到空 repos（`params.get("repos") or []` → 空 → `repo_block` 落到源名兜底行），**不报错、静默空转**。修正结构注释与示例嵌套一致，属本 change 核心价值（教学载体与代码契约不漂移——delta 以「平铺键 MUST NOT 示例」钉死）。
- **radar 消费面必填（条目全集的来源）**：`read_marker` 无条件取 `source["marker"]`（`:305-306`，`stage_radar` 每源都调 `:1612`）、`discover_today_new` 取 `source["content_glob"]`（`:322`）——缺即 KeyError。故完整条目 = `name` / `kind` / `root` / `content_glob` / `params{repos,window}` / `target_projects` / `marker`，与 ADR-0007 ④ 条目（`docs/adr/0007-multi-source-radar.md:100-106`）同构；`marker` 相对 `PA_HOME`（= 仓根 `.project-auto/`，`run_daily.py:101`）。`target_projects` 在示例中留占位并注记缺省语义（不喂任何项目——防新源误喂）。
- **window 形态**：`Nd/Nh`（load_sources 对 forge-repo 装载期校验 `:252-253`；github-repo 缺省 `7d`），示例取 `"7d"` 与 ADR-0007 一致。
- **kind 清单补 `forge-repo`**：add-forge-adapter 已把该 kind 写进 forge-watch spec，样例注释滞后一词——随本次注释修正一并对齐（纯注释零行为）。

## D3 delta 归属：template-bootstrap ADDED，而非 forge-watch、而非无 delta

- **勘察结论（为何不是 forge-watch）**：无 requirement 管辖「条目内容」——监控哪些仓是实例数据；forge-watch 管的是 **kind 契约**（forge-repo schema 装载校验 + 「既有 `kind: github-repo` SHALL 原样保留」，`openspec/specs/forge-watch/spec.md:10`），本 change 对任何 kind 行为零触碰 → 对 forge-watch 无 delta。
- **零 delta 分支被工具现实排除**：openspec 1.4.1 `--strict` 实测拒绝零 delta change（首轮 `exit=1`，报「Change must have at least one delta」）——「无 delta 则 Non-Goals 说明」与「validate 跑到绿」在本仓工具链下互斥，只能选有 delta + 绿。
- **template-bootstrap 归属三重依据**：① `bootstrap.sh:63` 装本样例，「干净目录初始化」requirement 的「sources 空模板」语义即指此文件的安装行为；② 同 capability 既有「样例覆盖面」requirement 专门管 profile.example.yaml 的教学形态——sources.yaml 是同一步骤装出的姊妹样例，同域有直接先例；③ add-executor-eng-skills 先例：仓内工件 → ADDED requirement 钉契约（executor 技能面），本 change 同构。
- **delta 刻意只钉防漂移复发面**：空模板活段 + ≥1 个 `params` 嵌套的 github-repo 全集示例 + 平铺键 MUST NOT + 「样例 MUST NOT 改变装载器行为」。**不钉具体仓号**——didi/helixharness 是示例内容不是契约，日后换示例仓不违 delta。

## D4 激活留白与参考克隆钉定

- **各机激活 = 不入仓任务**（tasks 2.x 留 `[ ]`）：本机形态仓根 `.project-auto/sources.yaml`、hub 形态 `HUB_DATA_ROOT/<project>/sources.yaml`，落活条目后 `--from-stage fetch --to-stage fetch` 单段冒烟（`STAGES` 含 `fetch`，`run_daily.py:131`；前提 gh CLI 已登录——github-repo kind 的既定形态是 persona 持 Bash 跑 gh）。
- **参考克隆钉定**：didi/helixharness main @ `44ffc536f73d8af525b4795ad10384c516898576`（2026-10-30，`git -C` 于 vault 外只读克隆取，remote 已核实 `github.com/didi/helixharness.git`）——用途是「信号价值论证」的事实基线可复现（协议红线 / eval ledger / 平台评测 env 均出自该克隆源码与 ask 勘察简报），**不是**镜像内容钉定：把插件内容快照进 executor 镜像是独立 change 的事（走 add-executor-eng-skills 模板：快照 + PROVENANCE + Dockerfile COPY + tag bump）。
- **遥测红线不冲突**：helix 的 consent 默认 disabled、不自动提交推送、配置地址不等于授权——本 change 只读跟踪其**公开仓活动**（commits/pulls），不涉 eval ledger 回收与评测面（proposal Non-Goals 显式）。

## 验证口径（全部本轮实测）

1. 样例空模板语义：`yaml.safe_load` 断言活段 == `{'sources': []}`——通过。
2. 教学条目可活化：示例块剥注释、占位符换真实项目名后过**真实** `load_sources()`（重绑 `PA_HOME`/`SOURCES_FILE` 指向临时目录），断言字段齐 + `github_repo_prompt` 产出含 `didi/helixharness` 与 `7d`——通过。
3. `openspec validate add-helix-signal-source --strict --no-interactive`——exit=0 绿（首轮零 delta 曾 exit=1，D3 记录）。
