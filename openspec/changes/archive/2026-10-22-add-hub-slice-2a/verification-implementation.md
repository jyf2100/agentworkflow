# 实施验证（/opsx:apply，2026-10-21）

上接 `verification-ontological-audit.md`（规约阶段审核 + 六项修正落地）。本文记录实施阶段证据：质量门、TDD 轨迹、实施中坐实的真 bug 与修正、compose 真机冒烟。

## 一、质量门

- `bash hub/quality.sh` 全绿：compileall + pytest + ruff（E9+F）零告警。
- hub 测试 66（切片 1 止）→ 92（组 1-4：vault 9 + cli 2 + lifecycle 4 + daemon 5 + db 并发 1 + api lifespan 2 + serve 3）→ 93（5.3 追加 cli 入口块 subprocess 防回归）。
- TDD 轨迹：组 1-4 每组先测（RED 确认）后码（GREEN）。

## 二、实施中坐实的真 bug（全部修正 + 防回归）

1. **sqlite 跨线程竞态**（组 3）：`check_same_thread=False` 只解除 Python 层限制，同连接对象跨线程并发 execute/commit 仍是竞态——全量跑挂 `OperationalError('cannot commit - no transaction is active')` + `SystemError`。修正：`db.py` 模块级 `@_locked`（threading.Lock）串行化「语句+fetch+commit」，覆盖全部 18 方法；与 WAL/busy_timeout 正交。多线程写压测（writer×40 + reader×40）三遍全过。
2. **serve.py main 时序错**（组 4）：`main()` 里装 `daemon.install_signal_handlers(app.state.stop_event)`——stop_event 由 lifespan 赋值，main 时刻不存在（AttributeError）；且 compose 形态 uvicorn 自收 SIGTERM 走 lifespan 路由，裸装多余。修正：删除该行，注释钉「仅服务不经 uvicorn 的嵌入场景」。
3. **cli.py 缺 `__main__` 入口块**（5.3 真机坐实）：`python3 cli.py status` 只 import 模块即静默退出（rc=0 零输出）——测试全走 `cli.main([...])` 直调故未暴露。修正：补入口块 + subprocess 防回归测试（`test_cli_entrypoint_runs_as_script`：直跑必须见「共 0 项」输出）。
4. **Debian trixie `docker.io` 包不含 docker CLI**（5.3 真机坐实）：镜像 build 过、`dpkg -l` 有 docker.io，但 `/usr/bin/docker` 不存在（只有 docker-init）——hub 容器内派发报 `No such file or directory: 'docker'`。修正：Dockerfile 改装 `docker-cli` 包（只要 socket 客户端，无 daemon 更小更精确）。
5. **socket 属主权限**（5.3 真机坐实）：hub 容器 uid=1001 连宿主 `/var/run/docker.sock`（root:docker 0660）被拒 `connect permission denied`。修正：compose `group_add: "${HUB_DOCKER_GID:-999}"`（本机实测 gid 999，env 可覆盖）。
6. **`image_exists` 把 daemon 不可达误分类为镜像缺失**（5.3 真机坐实）：socket 权限被拒（UNKNOWN）时 rc≠0 被当 NOT_FOUND——队列项进 fail 终态报「镜像缺失：pa-executor:v0.1.3（先手动 build…）」，与宿主镜像在库的事实矛盾、带偏排障方向。违反三态远程查询精神（spec: fail-safe-dispatch）。修正：`DockerRunner.image_exists` rc≠0 时查 stderr——daemon 不可达特征（cannot connect / permission denied）抛 RuntimeError（走 note_error 重试路径），仅真 `No such object` 返回 False。TDD：两特征串 ×「绝不进 ImageMissingError 终态」断言。

## 三、与规约的口径偏差（已记录，非漂移）

- **base 镜像 python:3.11-slim**（tasks 5.1 字面是 3.12-slim）：mirror.aliyuncs.com 拉不动 3.12（403）；3.11-slim 本机已有且符合 pa 全家 Python ≥3.11 口径。
- 其余 delta requirement/scenario 逐条按 tasks 1.1-6.2 落地，无范围增删。

## 四、compose 真机冒烟（5.3 + 6.2，2026-10-21）

环境：`/tmp/wf/data` 内外同路径 bind（compose override `volumes: !override` + `HUB_DATA_ROOT`）；fixture = proj-a boot inputs（profiles/proj-a.yaml + sources.yaml）+ PRD probe.md（frontmatter project/slug）；`chown -R 1001:1001`（经 root 容器，无宿主 sudo）；执行镜像 `pa-executor:v0.1.3`。

**5.3 属主对齐判据 ✓**：
- compose up → 容器内 `cli.py enqueue` → daemon tick 5s 内派发执行容器（socket 兄弟容器，`--from-stage inject` 生产 cmd）。
- 执行容器真写宿主 state 卷：`state/prd/proj-a/20261021_compose-probe.md` + `state/prd_manifest_20261021.json` + `.run.lock` 全部落宿主，**属主全部 1001:1001**（uid 对齐实证——镜像契约收口测试的 compose 形态复跑）。
- 派发生命周期顺带实证：`reconcile_running` 记账非零退（137 → attempt++ → 重派）→ 重派超限放弃终态（max_retries 语义）；env-file 注入经容器内 env 兜底路径（无 vault key 形态）。

**6.2 kill/重启自愈观察 ✓（C7）**：
- 容器内 `kill -9 1` 模拟进程崩溃 → `restart: unless-stopped` 自动拉起（uvicorn 重起、lifespan startup complete、daemon 线程恢复）。
- 拉起后 `reconcile_startup` 幂等对账：runs 索引行数不变（无重复建行）、队列状态不变、无重复派发。
- 运维边界坐实：`docker kill`/`docker stop` 是显式操作，restart 策略**不**覆盖——显式停后须手动 `docker start`（已记 RUNBOOK §13）。

**设计事实记录（非 bug）**：lifecycle 派发 cmd 为生产语义 `--from-stage inject`（inject 起点后继续全流程 critic/dispatch/report），与测试探针 `--to-stage inject` 截断形态不同——真部署跑全流程即本意；验证用假凭据时后续段会失败，属预期。

## 五、镜像事实

- `pa-hub:v0.2.0` / `deploy-pa-hub:latest`：python:3.11-slim + docker-cli + fastapi/uvicorn/cryptography/pypinyin，USER 1001:1001，ENTRYPOINT serve.py，~408MB。
- 执行镜像 `pa-executor:v0.1.3` 本机在（26h 前收口实测同款）。

## 六、最终质量门

`bash hub/quality.sh` 全绿：compileall + **94 passed**（66 → 94：vault 9 + cli 3 + lifecycle 4 + daemon 5 + db 并发 1 + api lifespan 2 + serve 3 + runner 三态 1）+ ruff（E9+F）零告警。

## 七、收口后本体论审核（2026-10-22，sync 落成后）

**审核对象**：sync 落成的三份主 specs × hub 实现 × 部署物 × RUNBOOK §13。方法：实体范畴边界、声明不变式与实现事实咬合、sync 产物与 delta 原文忠实度（diff 机械确认）、部署三步序/socket 权限/凭据通道口径一致性。

**正面确认**：
- sync 忠实度：三份（execution-hub 改名替换+追加 / hub-credential-vault / hub-deployment）requirement 正文与 scenario 逐字一致，validate 19 passed。
- 凭据通道不变式三方咬合（不落 DB/日志/meta、0600 env-file 即弃、无明文枚举面）；部署三步序三方一致（spec scenario ↔ RUNBOOK ↔ compose 头注释）；崩溃自愈 scenario ↔ kill 实证；范畴边界（兄弟容器不进 compose、保险箱全局层 vs per-project data、控制面/目标面镜像正交）延续成立。

**分级发现与裁决（用户 2026-10-22 裁决：B 全修 + C 随手带上）**：
- **B1** hub-deployment spec 缺 socket 属主对齐事实（uid=1001 连 root:docker 0660 须 group_add 宿主 gid；5.3 真机坐实，compose/RUNBOOK 已有、spec 未回写）→ 已修：「compose 单常驻服务」requirement 补 socket 属主 SHALL + 新 scenario「socket 属主对齐补组」。
- **B2** execution-hub「排度常驻服务」SHALL「完成当前迭代后停止循环」强于实现保证（lifespan join(10) 有界 + grace 30s；迭代卡 docker run 可至 120s）→ 已修：正文改「有界等待当前迭代收尾……超界随进程退出、悬挂 run 由重启对账归档兜底（durable 口径：退出与崩溃同靠对账收敛）」，scenario「优雅退出不动容器」同步两分支；RUNBOOK §13 优雅退出行同口径对齐（join 上限 10s 显式化）。
- **C3** F13「镜像缺失 fail」未声明 daemon 不可达分流 → 已修：正文补三态分流 SHALL（UNKNOWN 走重试、MUST NOT 误判 NOT_FOUND）+ 新 scenario「daemon 不可达不是镜像缺失」（对齐 fail-safe-dispatch 三态不变式）。
- **C4** RUNBOOK §13 补 data 卷属主不对齐症状（每 tick `[daemon][ALERT]` PermissionError、队列项永留 pending；核属主补跑三步②）。
- **C5**（提醒不落地）hub 新术语（保险箱/master key/排度常驻/兄弟容器）并入已挂账 F3 债（CONTEXT.md 术语第三次修订）。

**复跑**：`openspec validate --specs` 19 passed（与修正前持平）。
