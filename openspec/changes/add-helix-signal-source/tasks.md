# Tasks: add-helix-signal-source

## 1. 示例入库（sample 教学载体，随仓走）

- [x] 1.1 `Projects/项目推进流水线/bootstrap/samples/sources.yaml` 加 github-repo 完整示例条目（didi/helixharness）：注释教学形态、live 段 `sources: []` 保持空（template 不替实例默认跟踪）；字段照 ADR-0007 条目惯例全集——`name: helix-watch` / `kind: github-repo` / `root: Knowledge/github-watch/` / `content_glob: "**/[0-9]*.md"` / `params: {repos: [didi/helixharness], window: "7d"}` / `target_projects`（占位 + 缺省不喂说明）/ `marker: state/consumed_helix_watch`
- [x] 1.2 同文件既有结构注释修正（纯注释零行为）：kind 专属参数平铺 → `params` 嵌套（对齐消费面 run_daily.py `github_repo_prompt`/`wechat_url_prompt`/`fetch_prompt` 只读 `params.*`）；`topic` → 实际键名 `prompts`；kind 清单补 `forge-repo`
- [x] 1.3 spec delta：template-bootstrap ADDED requirement「sources 样例教学形态」（空模板活段 + params 嵌套全集示例 + 平铺键 MUST NOT；三个 scenario 覆盖照抄可用/空模板不破/嵌套与消费面一致）
- [x] 1.4 样例完整性冒烟：python yaml 解析样本文件 `sources == []`；示例块活化（剥注释 + 占位符换真名）后过真实 `load_sources()` 且 `github_repo_prompt` 读到 `didi/helixharness` + `7d`
- [x] 1.5 `openspec validate add-helix-signal-source --strict --no-interactive` 绿（hub/quality.sh 由编排统一跑，不在此列）

## 2. 各机运行时激活（gitignore 实例数据，不入仓——留待各机自行落地）

- [x] 2.1 本机形态：仓根 `.project-auto/sources.yaml`（缺则从 bootstrap sample 复制起）照 1.1 注释块落活条目，`target_projects` 填本机已准入（admission: true）项目名
  - 2026-10-31 实证：vault 根 `.project-auto/sources.yaml` 从 bootstrap sample 复制起、照 1.1 注释块落 helix-watch 活条目（字段全集 name/kind/root/content_glob/params/marker）；真实 `load_sources()` 载入 + `github_repo_prompt` 读到 `didi/helixharness`+`7d`。本机 vault 根 `.project-auto/profiles/` 不存在（admission:true 项目为零）→ `target_projects: []` 是唯一诚实填法（缺省不喂，防误喂；编造项目名=引用不存在的 profile），准入项目后补名即接通 radar
- [x] 2.2 hub 形态：`HUB_DATA_ROOT/<project>/sources.yaml` 落同款活条目（项目启动必读面 = profiles/ + sources.yaml）
  - 2026-10-31 实证：`/data/pa/cc-web-control/sources.yaml` 落同款活条目 `target_projects: [cc-web-control]`（owner hiroute:containerd 644 保持）。选择理由：五个带 sources.yaml 的 hub 项目中唯一 admission:true + dev_agent_ready:true + type:code 的真实 dev-loop 项目、主题最近（Claude Code 生态工具仓 ←→ helixharness 插件框架治理信号）；排除项——pa-qa-gitlab one_liner 自称「不参与信号匹配」、pa-panel-form-probe 为表单探针、aigw 无 dev_agent_ready/type:code 且主题无关（gitcode AI 网关编译部署）、runner-pool-verify profiles/ 空未准入、/data/pa/vault 仅 credentials.json 非流水线项目。真实 `load_sources`/`load_profiles` 重绑 hub 目录验证：radar 将喂 = target_projects ∩ 已准入 profiles 命中 cc-web-control，`radar_prompt` 内嵌其 match_surface.one_liner「cc-web-control优化」
- [x] 2.3 激活后冒烟：`python3 scripts/run_daily.py --from-stage fetch --to-stage fetch`——fetch 段日志见 helix-watch 源产出（前提：gh CLI 已登录）；radar 侧确认 target_projects 命中项目的 match_surface 比对生效
  - 2026-10-31 实证（vault 根跑，gh 已登录 jyf2100）：`sources=['helix-watch']` 装载后 `[fetch] ✅ helix-watch → Knowledge/github-watch/20261031_didi-helixharness-7d-digest-7-commits-plugin-imp.md`（didi/helixharness 7d 真实活动：7 commits/4 PRs；fetch_20261031.json produced=1、failed_sources=[]）。radar 侧：本地形态动态跑 `--from-stage radar --to-stage radar`——今日新=1 被 content_glob 发现、profiles 空零喂、marker 不 bump（无有效目标保持可发现，与 run_daily.py stage_radar 设计一致）；hub 形态 target∩profiles 命中 cc-web-control 且 radar_prompt 内嵌 match_surface（真实函数验证）。备注：config 路由 pa-fetch-github-repo→qwen3.6-35b 后端 172.32.153.184:8896 拒连（基础设施不可达），用流水线自带 canary 覆盖 `PA_PERSONA_MODEL_PA_FETCH_GITHUB_REPO=sonnet`（env 优先于 config，run_daily.py:511-515）跑通；--force 因首跑失败已落 fetch json（复用门设计内行为）
