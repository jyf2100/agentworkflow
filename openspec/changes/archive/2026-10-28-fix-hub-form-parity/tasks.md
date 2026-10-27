# Tasks: fix-hub-form-parity

## 1. report 出口接通（execution-hub 段范围 inject→report）

- [x] 1.1 `hub/lifecycle.py` 派发命令补 `--to-stage report`（cmd 在 `--from-stage inject --inject-prd ... --stamp` 基础上增补该 flag；`PA_REPORT_DIR` 注入与 `PA_*` 透传既有代码零改动）
- [x] 1.2 单测：断言派发 cmd 含 `("--from-stage", "inject")` 与 `("--to-stage", "report")` 相邻对（照既有 lifecycle cmd 断言测试的写法，`hub/` 测试目录）
- [x] 1.3 `bash hub/quality.sh` 全绿（从仓库根）

## 2. 文档 / 声明对齐

- [x] 2.1 `Projects/项目推进流水线/RUNBOOK.md`：容器形态注记——SMTP 在容器内 fail-open 告警属预期（通知面=hub-notifications MM bot，勿配 SMTP env）；模型路由容器形态 env-only 配置示例（hub.env 配 `PA_PERSONA_MODEL_<AGENT>` / `PA_DEV_MODEL`，改后 `docker compose up -d` 重建 hub 进程即生效，无需重发镜像）
- [x] 2.2 核对 `Projects/项目推进流水线/SPEC.md` / `CLAUDE.md` 若有「执行链 inject→dispatch」措辞则同步为 inject→report（hub 形态语境下）——SPEC.md:207 为手动注入**通用用法示例**（dispatch 停仍合法），hub 段范围权威在 execution-hub spec + RUNBOOK §17，判定无需改

## 3. profile 样例补 forge 教学

- [x] 3.1 `Projects/项目推进流水线/bootstrap/samples/profile.example.yaml` 补 forge 形态注释段（`forge` / `gitlab_host` / `repo` 全路径三键 + 两种 `repo` 语义对照注明）；纯注释/样例零行为变更，跑 `bash scripts/quality.sh` 确认无破坏

## 4. 真机验证（184 hub）

- [x] 4.1 容器内 run 一次探针（drop 形态便宜 PRD 或 QA 仓小 PRD），确认容器命令含 `--to-stage report` 且 `state/reports/` 出现该 stamp 报告产物；顺带确认 report 段 SMTP 告警行出现且 run 终态不受影响（fail-open 验证）
  - #48 drop 探针 done attempt=0：cmd `["--from-stage","inject","--to-stage","report",...]` ✓、`state/reports/项目推进报告_20261027m5.md` 落卷 ✓（**首次**出现报告产物）、run succeeded exit 0 ✓
  - SMTP 实测**不适用**：drop-only 走「全绿不投递」分支（SPEC §8），根本不触 SMTP——比 fail-open 预期更干净；fail-open 告警行只在有邮件内容的 run（merged PR 形态）触发，留给下次真 run 自然验证
  - hub 镜像 rebuild + `--force-recreate`（代码烤镜像非挂载，`up -d --build` 后容器仍 Running 未换镜像，须 force）；TAG v0.1.7 保留
- [x] 4.2 面板 run 详情页确认该 run 报告可渲染（对照 hub-panel「报告缺失显无报告」语义，历史 run 仍显「无报告」不回填）
  - 数据面直证：run id=53 stamp=20261027m5，api.py 同款 glob 命中 `项目推进报告_20261027m5.md` → panel `_esc(report_md)` 渲染成立（纯机械零语义）；HTTP 面板 302 登录守卫在，肉眼确认走浏览器（既有入口）

## 5. 面板建项目补 forge 形态（用户指出界面缺口 2026-10-27）

- [x] 5.1 `hub/projects.py create_project` 补 forge/gitlab_host 可选参数：gitlab 校验 host 必填（带 scheme）+ repo 全路径形态（不走 GitHub URL 归一）；gitcode 两级；github 缺省落盘键集与行为零变化；非法值抛 ProjectSetupError
  - 落点：`forge=""`/`"github"` 均归一缺省（老表单/脚本直 POST 逐字节回归）；新增 `normalize_forge_repo`（首/尾/连续 `/` 全拒——`/abs/path` 本地 clone 语义撞车即拒；gitlab ≥2 段多级、gitcode 恰 2 级）
- [x] 5.2 `hub/panel.py` 建项目表单补 forge select（github 缺省/gitlab/gitcode）+ gitlab_host input + repo 提示语双形态说明；`hub/api.py` POST 透传新键
- [x] 5.3 测试：gitlab 建档落盘三键 / gitlab 缺 host 422 / gitcode 两级 / github 缺省回归锚 / 非法 forge 拒（`hub/test_projects.py` + `hub/test_api.py`）
- [x] 5.4 `bash hub/quality.sh` 全绿；hub 镜像 rebuild + force-recreate 后真机面板建一个 gitlab 探针项目确认落盘
  - 质量门全绿（compile + pytest 250 passed + ruff）；184 rebuild + force-recreate 后真机面板全链路：POST forge=gitlab 201「已建档」，`/data/pa/pa-panel-form-probe/profiles/*.yaml` 落卷三键（forge/gitlab_host/repo 全路径）；负面面 gitlab 缺 host → 422 回显「gitlab_host 必填」零目录残留；会话 token 容器内签名经 shell 变量流式传递（secret 不出容器）
