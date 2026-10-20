# 验证笔记 7：端到端干跑（release→bootstrap→admit→webapp/inject）× 双侧质量门

> 日期：2026-10-20 ｜ 对应 tasks 7.1/7.2。全程在 release 干跑副本 `/tmp/pa-tmpl-63`（upstream a8e9e9e 重投影后）+ 隔离 venv（`/tmp/pa63-venv`，零污染全局 editable 安装）。

## 1. 端到端链路证据（7.1）

| 环节 | 命令/入口 | 结果 |
|---|---|---|
| release | `release_template.sh --out /tmp/pa-tmpl-63` | 全绿（副本内 quality 2016 passed + grep 门禁零命中 + 副本根 `CLAUDE.md` 含「任务通道指导」+ RUNBOOK §0 投影） |
| bootstrap | 副本根 `PATH=venv bash bootstrap.sh --non-interactive` | exit 0：真 claude CLI 冒烟过、依赖入 venv、仓根 `.project-auto/sources.yaml` + `pa/config/` 三样例落位、cron/SMTP 缺省关 |
| admit | `scripts/admit_repo.py demo-mock/nonexistent-repo --repo-dir /tmp/pa63-mock-repo` | 真 gh 通道三态齐现：protection **UNKNOWN ⚠**（fail-safe 阻断 exit 2）｜test entry **NOT_FOUND ✗**（可行动指引）｜commit identity **FOUND ✓**（--repo-dir 本地真验）；CLAUDE.md 建议起点打印且不写目标仓 |
| webapp→inject | 面板 127.0.0.1:8363（`pipeline_cmd` 前缀 `--to-stage inject --no-notify` 封机械段）→ `POST /api/inject`（multipart `file`） | accepted → spawn run_daily（pid 445564）→ `stage inject→inject`、`sources=[]  profiles=['demo']`、`[inject] ✓ 注入 → prd_manifest_20261020.json` |

**inject 终态五要素**：`state/prd/demo/20261020_pilot-dryrun-demo.md` 落盘（frontmatter 补全 date/signal/round/slug）✓｜`prd_manifest_20261020.json` ✓｜用户原 PRD 未动 ✓｜inbox 副本 `cmp` 字节一致 ✓｜面板 status 回落 idle ✓。

## 2. 干跑抓出并修复的真 bug：实例数据落位错位

**症状**（第一次注入）：run_daily 崩于 `load_sources FileNotFoundError: /tmp/pa-tmpl-63/.project-auto/sources.yaml`。

**根因**：run_daily `PA_HOME = VAULT_ROOT / ".project-auto"`（`run_daily.py:91`，**仓根**——vault 实况 `agentworkflow/.project-auto` 佐证）；而 bootstrap（task 3.x 修正时）装到 `Projects/项目推进流水线/.project-auto/`（pa 下）。profiles/sources 全错一层。

**修复**（vault 侧 5 文件，quality 2024 passed 锁定）：
- `bootstrap/bootstrap.sh` [3]：`INSTANCE_DIR="$TOP/.project-auto"`（sources.yaml + profiles/ 落仓根；`config/` 仍在 pa 内——纯样例收纳，无代码消费路径）
- `scripts/test_bootstrap.py`：两处断言改根（`root/.project-auto/sources.yaml`）
- `release/docs/CLAUDE.template.md`：合法本地数据/采集源提法改「仓根 `.project-auto/`」
- `bootstrap/samples/profile.example.yaml` 首行注释 + bootstrap 头部注释 [3] 行：补「仓根」
- webapp 与 run_daily 本就一致（`app.py:126` 同为 `VAULT_ROOT/.project-auto/state`），无需动

**复验**：重投影副本 → 重跑 bootstrap（sources/profiles 落仓根 ✓）→ 重注入（`profiles=['demo']` 读到、inject 段成功）。

## 3. 双侧质量门（7.2）

- vault：`bash scripts/quality.sh` → 2024 passed, 5 xfailed + ruff 零告警（bug 修复后重跑）。
- 副本：release [4] 在副本内 quality.sh → 2016 passed + grep 门禁零命中（重投影后再证）。
- 显式 `ruff check scripts`：All checks passed（双侧）。

## 4. 遗留

7.3-7.5（试点执行/复盘/学习回流）依赖真实同事 + 真实目标仓，待用户安排试点后推进；template 仓托管位置仍是 design Open Question。

## 5. 本体论 review 衍生修复（2026-10-20，用户批准执行）

四维 review（实体/范畴/关系/不变式）发现 F1-F3，修后 quality 2024 passed + release 干跑（门禁含新 pattern）全绿：

- **F1 门禁漏上游身份标识**：`PERSONAL_PATTERNS` 增 `jyf2100` + `_PERSONAL_RES` 增复验项；transform 增 11 条规则（`_UPSTREAM_ID_PAIRS`）清除投影 12 处操作性残留——runtime_evidence.py 5 处 drill 默认参数 + 1 注释（占位 `OWNER/REPO`，误跑 → gh 404 → UNKNOWN fail-safe）、canary_automerge.sh 默认仓、run_daily.py docstring 示例、test_single_flight.py 2 处字面、RUNBOOK 历史文字。操作性残留全含 `jyf2100`、声明性引用（CONTEXT.md:12 / RELEASE_NOTE 的 `agentworkflow`）全不含——单 pattern 精确切分两类。vault 源不动（真源保留维护者便利）。
- **F2 术语表缺新实体**：CONTEXT.md 增「产品化拓扑」节——投影 (Projection) / template 实例 (Template Instance) / 回流 (Backport) 三词条（含 _Avoid_ 反例与 [[大脑]] 关联）。
- **F3 范畴滑落**：RUNBOOK §11 投影「vault checkout」→「控制面仓 checkout」（`_RUNBOOK_TOPOLOGY_NEW` 2 处）。
- F4（「仓根」相对提法不统一）记录不修。

## 6. 复审（fresh 投影 223 文件）衍生修复：F5 机器路径（用户批准最小修）

上轮清点只 grep `jyf2100`，漏不含该前缀的同类标识。四维复审三维全绿（范畴：bootstrap/CLAUDE.template 的 vault 提法均为上游真源/回流合法指称；关系：install_sample `[[ ! -e ]]` 不覆盖实例数据、profile 样例装 config/ 防误投递、回流 vs 学习回路两对象无冲突；不变式：F1 后 jyf2100=0 维持）。唯一新发现：

- **F5 门禁缺「机器路径」类**：投影 `/mnt/disk01/workspaces/worksummary/…` 3 处（canary_automerge.sh:36 CC_WEB 默认值、run_daily.py:433 PA_PONYTAIL_DIR 默认值、test_run_persona_contract.py:133 断言镜像）——与门禁既有 `/Users/roc` 同类（机器身份标识），linux 服务器形态漏网。零外向风险（同事机器无该路径 → fail-safe），但属信息暴露面。
- **修复**：`PERSONAL_PATTERNS` 增 `/mnt/disk01` + `_PERSONAL_RES` 复验同步；transform 增 3 条规则（`_UPSTREAM_ID_PAIRS` 追加）——CC_WEB 改 `${PA_CC_WEB:?须显式设置}`（未设报错退出）、ponytail 默认值与断言镜像同步改 `~/pa-ponytail/ponytail`（投影内测试自洽绿）。vault 源不动。
- **`cc-web-control` 裸串 21 文件记录不修**（fixture 字面量 ~16 + 声明性举例 ~5：CONTEXT.md:28 词条举例、RUNBOOK canary 纪录、pa-prd.md:34 契约示例、graph_pa.py help）——零外向风险下收益/成本倒挂（多处同串 + apply_rule 单点语义 = 40+ 条规则），内部 template repo 定位下可接受。

**F5 后终验**：vault quality 2024 passed + ruff 零告警；release 干跑（门禁含 `/mnt/disk01`）全绿；投影 `/mnt/disk01` 0 命中、`jyf2100` 维持 0、三处占位落地、cc-web-control 维持 21（记录不修）。

## 7. 7.3 试点启动：template 仓建立 + 首两轮发布（2026-10-20）

**三项决策（用户）**：试点仓 = `jyf2100/cc-session-mgr`（私有 TS 业务仓，目标面，流水线对它开 PR）；template 仓 = 新建私有 `jyf2100/pa-pipeline-template`；执行人 = 用户先自跑通（7.3 部分达成——「没接触过 pa 的人能否跑通」信号留二期同事试点）。

**发布记录**：
- v0.1.0：release 全链绿（含副本 quality + 门禁）→ publish init/commit/tag → push。**抓出真 bug #1**：`git init` 默认分支 master ≠ 脚本提示的 `push main`（源引用无匹配）→ 修复 `init -q -b main`。
- **F6（产品文档，v0.1.0 携带）**：`profile.example.yaml` 的 `repo` 字段注释教成「owner/repo 远程标识」，实际语义（`repo_owner_repo` 用 `git -C repo remote get-url` 提取 + `dispatch_one` 以 `Path(repo)/.worktrees` 建 worktree）是**目标仓本地 clone 绝对路径**——同事照填必失败（相对路径 `git -C` 无仓 → 建 worktree fail）。CONTEXT.md:8「本地路径由实例 profiles 配置」本就正确，样例注释与之矛盾。修复：样例值/注释改绝对路径语义 + CLAUDE.template.md §profiles 字段说明补准（repo = 本地 clone 路径）。
- v0.1.1：两修复随行（bootstrap/release 单测回归 14 passed + 全链绿）→ force push（快照式独立 init 发布模型，无共同祖先；旧 commit 由 v0.1.0 tag 锚定不丢）。
- 远端终态：main@v0.1.1；双 tag 落地；samples/（profile/sources/notify 三样例）与两处 F6 修复均验证在 v0.1.1 tag 内容内。

## 8. 7.3 试点全链达成：inject → critic → dispatch → 真 PR（2026-10-20，用户授权代跑）

**链路终态**（run7，`--from-stage dispatch --force`）：PRD 注入 ✓ → critic **PASS**（$0.1385，1 minor：web 面板条款措辞与佐证§5有张力，不影响可执行性）→ 准入三态全过 → dev loop（72 turns，$2.00，commit `2d76828` 12 文件 +414/−61）→ 独立验证绿（fresh worktree npm ci+test，43 文件/555 tests）→ pa-verify r1 PASS（$0.3941）→ **PR #1 OPEN**：`https://github.com/jyf2100/cc-session-mgr/pull/1`（auto/20261020-tui-light-theme → main，+563/−61）。dev 产出质量点：light 选色按 WCAG 对比度计算、palette 单测覆盖 dark 默认/light 生效/未知回退、web 面板按 PRD 第 4 条记 TODO（其 CSS 与终端独立）。

**验收三件**：① PR OPEN ✓（上）；② 目标仓主工作区仅 `?? .worktrees/`（流水线工作目录，与 vault 实况同形态），main 与 origin 零漂移、scripts/ 零本地改动 ✓；③ 实例 state 产物完整（prd/*.md + prd_gate_20261020.json + dispatch_20261020.json + runs/ 下 dev log/r1.diff/testout）✓。

**过程记录**（三处环境前置由用户裁决）：cc-session-mgr 转公开 + main 配分支保护（enforce_admins+PR 必经+禁 force push；批准数 0——单人仓 PR 由本人令牌开出，配 1 无法自批）。

**试点抓出的产品缺陷 F7-F11（随 v0.1.2 修）**：
- **F7** 分段放行断裂：`--from-stage critic` 单独跑「无 PRD 跳过」——manifest 仅在 prd/inject 段赋值，critic 无盘读兜底（dispatch 有 3758-3761 兜底）。当下正确用法 = 一条龙 `--from-stage inject --inject-prd <file>`。
- **F8** `profile.example.yaml` 的 `match_surface` 教 list，代码（radar:594-618 / critic:647-650）硬要 dict `{one_liner, keywords}`——照样例填 → critic_prompt `AttributeError` 崩。
- **F9** 样例 `type` 教 `node | python（验证闸选测试驱动方式）`，但唯一消费点（run_daily:2347 准入门）只认 `code`；测试驱动实际是探测式（package.json→npm，否则重放 test_cmd，2907）。照样例填 → dispatch 必跳「profile 不满足」。
- **F10** `dispatch_{stamp}.json` 存在即整段复用（不重投），须 `--force`——新实例操作者易踩（我踩了）。
- **F11** 免费账户私有仓无法开分支保护（403 plan-gated）→ 三态判 UNKNOWN → fail-safe 硬阻断（正确执法），但 template 产品文档未声明目标仓前置条件「main 可开分支保护（公开仓或 Pro/组织私有仓）」。
- 另：PRD 被判 drop 的教训——critic 对「代码现状断言」要求 `source_path` 佐证（信息源文件含命令+输出原样），补上即 PASS；这是质量闸正确执法非 bug。

## 9. 7.4 试点复盘一页纸（E2，2026-10-20；数据源：实例 state 三件 + PR #1 现态）

**样本**：1 目标仓 × 1 手写 PRD × 1 dev 投递（stamp 20261020，7 次运行 run1-run7，其中 4 次为有效推进、3 次踩坑返工）。

| 维度 | 结果 |
|---|---|
| PRD 修订轮次 | 0（手写注入不走 prd 段；critic drop 后作为新 run 重注入，非 revise 路径） |
| critic 分布 | 成功执行 2 次：drop 1（佐证缺失，正确执法）+ pass 1（round 1 / revised=false）；另 2 次因 F8 样例错误崩溃（编排器侧 AttributeError，非 critic 判决） |
| verify 分布 | 1 投递全终态：r1 **pass**（独立验证绿 + pa-verify 双绿）；revise 0 / redo 0 |
| dev loop | 72 turns，off_track 0（方向抽查零纠偏），$2.00；critic $0.14 + verify $0.39 → **全程 LLM $2.53** |
| PR 终态 | **MERGED**（用户 2026-10-20 15:44 审合，2 commits）；revert/rework 30 天窗口 → **2026-11-19 前待观察（pending）** |
| 同事体感 | 无真实同事（按用户裁决留二期）；维护者代跑体感 = F7-F11 五坑全踩 + 佐证教训，坑均在**产品文档/样例层**，编排器执法层零误判 |

**结论**：链路语义层（三态准入、质量闸、测试门、verify 双闸、幂等）全程零误判零漏放；全部摩擦源自「template 教学层教错/没教」——正是产品化 change 的靶心，F7-F11 随 v0.1.2 修复后，二期真实同事试点的「新人能否跑通」信号才有效。

**E2 机制化判断**：1 实例样本量下人工聚合约 10 分钟（state 三件结构化良好），远未到成瓶颈；维持 E3 触发条件（≥3 实例）不变。

## 10. v0.1.2 发布（F7-F11 修复随行，2026-10-20）

- **F7**：`_manifest_disk_fallback`（run_daily.py，与 dispatch 段 prd_gate 盘读兜底对称）+ critic 段接线；TDD（test_critic_manifest_fallback.py 4 用例 RED→GREEN，含「不误读自增轮 manifest」fail-safe 用例）。
- **F8/F9**：profile.example.yaml `match_surface` 改 dict 形态、`type` 改 `code`（注释写明唯一合法值 + 测试驱动探测式与 type 无关）；CLAUDE.template.md §profiles「测试类型」旧措辞同步改准。
- **F10/F11**：CLAUDE.template.md 日常使用节加「目标仓前置条件」（main 可开分支保护；免费私有仓不可作目标仓）；FAQ 加「复用已有」条目（--force 语义 + --from-stage 续跑）。顺带修 76 行 state 路径漏改（pa 下 → 仓根）。
- **发布过程**：vault quality 2028 passed（+4 = F7 测试）→ release 全链绿 → push（快照式 force）→ 远端三 tag 齐（v0.1.2=37ba737）。
- **过程教训（F12，记录）**：投影经 `git ls-files` 展开目录 pathspec——**未 stage 的新文件静默漏出投影**（首次 v0.1.2 副本缺 test_critic_manifest_fallback.py，git add 后重发才入）。发布前新文件必须先 stage；未来可考虑 release 自检加「工作树 vs ls-files 差集告警」。

## 11. 7.5 学习回流（E3 本期处置，2026-10-20）

**Lessons 吸收状态盘点**（试点全部高价值 lessons 去向）：

| Lesson | 去向 | 状态 |
|---|---|---|
| F7-F11 五缺陷 | v0.1.2 产品修复（§10） | ✓ 已发布 |
| PRD 现状断言须佐证（run1-3 drop 根因） | CLAUDE.template.md「上下文准入两问」第 1 问补佐证文件写法（命令+输出原样、落 inbox、source_path 指向） | ✓ 本节补齐（随下次 release 随行） |
| generic profile 维护约定（回流路径 + 机制化触发） | profile.example.yaml 尾部维护注记（≥3 实例且人工成瓶颈再机制化；当前人工） | ✓ 本节补齐（随下次 release 随行） |
| F12（ls-files stage 盲区） | 维护者侧，§10 已记录；release 自检增强候选 | 登记不修 |
| 7.4 结论（语义层零误判、摩擦全在教学层） | 复盘一页纸 §9 | ✓ 已记录 |

**不单独发 v0.1.3**：两项补充均为文档/注释级、读者主要在二期同事试点前到位即可，随下次 release 自然随行（发布成本不为注释级改动单独支出）。
