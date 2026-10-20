# Tasks: productize-pa-template

> 依赖顺序：文档修复（发布前置）→ 发布链 → bootstrap → admit → webapp → 产品文档 → 集成验收。每个任务可单 session 完成且可独立验证。规约依据：本 change `specs/` 四 capability；落位依据：`design.md` D0-D7/E1-E3。

## 1. 发布前置：文档诚实性修复（F1/F2/F3/F5）

- [x] 1.1 F1+D3：CONTEXT.md `drop`/`triaged` 词条加现状标注（critic 三边界仍产 drop、langgraph tasks 4.2 未完成）；「大脑」词条范畴迁移为「控制面仓（每实例一个）」多实例；修正「无 remote」过时声明
- [x] 1.2 F2：SPEC.md frontmatter 改 `status: superseded` 加历史化注记（留 vault 不进 template）
- [x] 1.3 F3：RUNBOOK.md 与 CLAUDE.md 加部署拓扑节（本机双 checkout、cron 真身在 vault、路径真相）
- [x] 1.4 F5：CLAUDE.md persona 表补第 8 个 pa-progress，修正「7 个」计数
- [x] 1.5 交叉验证：术语现状标注与 run_daily.py/graph_pa_aggregate.py 实际行为逐条对上（引用 file:line）——证据见本 change `verification-1.5.md`

## 2. 转换脚本与发布链（template-release）

- [x] 2.1 `release/transform/` 转换脚本：profiles generic 化、SMTP env 驱动化、路径去 roc、术语范畴迁移——全部机械转换、幂等、输出可 diff
- [x] 2.2 `release/release_template.sh`：路径清单抽取（=内容契约）+ 转换 + 内容自检（清单校验/通道指导必备/规约层逐字节 diff）+ 发布门（template 副本跑 quality.sh + 去个人化 grep 门禁）+ release note 模板（注明上游 in-progress 状态）
- [x] 2.3 `pyproject.toml` 加 optional extras `webapp`（FastAPI 进独立依赖面，编排器核心零新依赖）
- [x] 2.4 release 单测：转换幂等性、grep 门禁负例（植入个人痕迹→发布失败）、内容契约负例（混入 SPEC.md→失败）、规约层漂移负例（篡改副本 spec→失败）
- [x] 2.5 对当前 HEAD 干跑 release 全绿（产 template 副本到临时目录，不推团队 git）

## 3. bootstrap 实例化（template-bootstrap）

- [x] 3.1 `bootstrap/bootstrap.sh` 干净目录初始化：建 config、`pip install -e ".[dev]"`、claude CLI 前置冒烟（--version + 端点连通）；任一失败非零退出带指引
- [x] 3.2 环境冲突检测（D4）：已有 `.claude/` 同名 persona / settings 交互点 → 逐项列出 + 默认拒绝覆盖，不静默合并
- [x] 3.3 本地改动检测（D1 机制化）：受检路径 `scripts/`、`.claude/agents/`、`openspec/` vs template tag 基线 diff；漂移→警告+diff 摘要+回流指引，不覆盖；`config/`、`profiles/`、`.project-auto/` 不在检测范围
- [x] 3.4 可选项缺省关闭：cron 交互可选（跳过即不写 crontab）、SMTP 通知默认关（报告落盘即止）
- [x] 3.5 bootstrap 冒烟测试：tmp 目录干净初始化全流程 + 篡改 scripts 告警负例 + mock claude 缺失失败路径

## 4. admit_repo 准入检查器（repo-admission）

- [x] 4.1 `scripts/admit_repo.py` 三态检查：branch protection / 测试入口 `scripts.test` / git commit 身份，每项 FOUND/NOT_FOUND/UNKNOWN；UNKNOWN=blocked+修复指引（复用 scripts 内 gh 三态封装）
- [x] 4.2 `gh api` 配 branch protection（平台操作白名单，与 dispatch 运行时实查对称）；操作结果三态回报，失败即 UNKNOWN
- [x] 4.3 不写目标面：缺失项（仓 CLAUDE.md/测试入口）输出待确认指引文本，无任何写目标仓文件的代码路径
- [x] 4.4 pytest 单测：mock 仓矩阵（无 protection/无 test script/身份缺失/查询超时 UNKNOWN）→ 各态断言

## 5. webapp 注入面板（prd-injection-console）

- [x] 5.1 `webapp/app.py` 上传端点：只收 markdown 文件（拒绝命令/非文件载荷）、落盘、subprocess 起 `run_daily.py --from-stage inject`（与 CLI 共享注入路径，不在面板进程内重实现编排）
- [x] 5.2 `.run.lock` 互斥复用：锁占用返回显式「进行中」响应，不排队不并发
- [x] 5.3 只读状态视图 + PR 列表：解析 `dispatch_*.json` 渲染当日终态；无任何 verdict 改写路径
- [x] 5.4 `webapp/static/index.html` 一页（上传/状态/PR 三区，零构建链零 node 依赖）；默认 bind 127.0.0.1
- [x] 5.5 pytest 单测：状态解析/注入触发 subprocess mock/锁互斥双触发/非文件拒绝；fail-open 验证（杀 webapp 进程，进行中 run 不受影响）

## 6. template 产品文档

- [x] 6.1 使用视角 CLAUDE.md 新写：安装/配置/FAQ + 回流约定首节（bug→vault 修复→下次 release）+ E1 通道指导节（「需新测试证据才走流水线」）+ 上下文准入两问 + 模型层前置说明（同事自己的 claude CLI）
- [x] 6.2 `config/` generic 演示 profile、sources 空模板、通知开关样例
- [x] 6.3 RUNBOOK template 版：部署拓扑、升级 = `git pull` + bootstrap 重跑（本地改动检测）、常见恢复入口

## 7. 集成验收与试点

- [x] 7.1 端到端干跑：release 产 template 副本 → bootstrap 干净目录初始化 → admit 对 mock 仓 → webapp 上传示例 PRD → 机械段 dry-run（`--limit` 封顶）走通全链
- [x] 7.2 双侧质量门：vault 与 template 副本 `bash scripts/quality.sh` 全绿 + `ruff check scripts` 零告警
- [x] 7.3 试点执行：真实目标仓 + 手写 PRD → 真 PR + 同事仓零 scripts 本地改动（2026-10-20 维护者自跑通达成：cc-session-mgr PR#1 独立验证绿、目标仓主工作区仅 .worktrees/；「没接触过 pa 的真实同事」信号按用户裁决留二期，见 verification-7 §8）
- [x] 7.4 试点复盘（E2，零新机制）：一页纸见 verification-7 §9（critic 2 次 drop1+pass1、verify r1 全绿、全程 LLM $2.53、PR#1 已合、30 天窗口至 2026-11-19 pending、体感=F7-F11 全在教学层）
- [x] 7.5 学习回路人工回流（E3 本期处置）：lessons 多数已随 v0.1.2 落产品（F7-F11）；余下两项补齐——佐证文件写法教学进 CLAUDE.template.md 两问、维护注记（回流路径 + 机制化触发 ≥3 实例且人工成瓶颈）进 profile.example.yaml 尾部；随下次 release 随行（见 verification-7 §11）
