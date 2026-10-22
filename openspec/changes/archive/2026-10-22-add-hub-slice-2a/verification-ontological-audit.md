# 本体论审核（规约阶段，2026-10-21）

审核对象：add-hub-slice-2a 四件 artifacts（proposal / design / 3 delta specs / tasks）。
方法：mattpocock-skills:domain-modeling——实体范畴边界、不变式与 F 系裁决及切片 1 主 spec 的一致性、与 2026-10-21 镜像契约收口实测事实的咬合、tasks 对 delta 的覆盖度。所有事实主张已 grep 源码/spec 坐实。

## 一、正面确认（咬合面成立）

1. **实体范畴边界清晰**：
   - 保险箱（`<data_root>/vault/`，全局层）与 per-project data（`<data_root>/<project>/`）分层正确——凭据是 hub 级实体，不是 project 级；
   - master key（宿主 0600 文件，gitignore）与密文（data 卷）分离存放，两个实体不混层；
   - pa-hub 镜像（控制面常驻物）与执行镜像（目标面即弃载体）正交，D6 零共享层与「控制面/目标面严格分离」（ADR-0001）同构。
2. **D1 范畴澄清正确且必要**：F8「compose 含两个服务」措辞把**运行时产物**（执行容器，每 run 动态挂载/env-file/stamp）误当**部署物**（静态 service 定义）。镜像契约收口实测（docker run 动态参数）证伪了静态表达。本体修正：执行容器不进 compose service，是 socket 兄弟容器。
3. **D3 兜底保住向后兼容不变式**：vault → env 降级路径使切片 1 部署形态零破坏（2.2 全量回归任务钉死实证）。
4. **D5 直接吃进实测事实**：uid=1001 对齐源自镜像契约收口的 PermissionError 复现，非拍脑袋。
5. **凭据不变式全面延续**：MUST NOT 落 DB/日志/meta；0600 env-file 即弃即删通道不动（RunSpec.env_file 接缝零变化）；明文只在内存——不新增明文落点。
6. **C5 已核实无 spec 漂移**：主 spec「队列排度」原文钉「DB 查询为权威判据 + .run.lock 防御层」，**未钉「单写者/单线程」**；D4 多线程方案（WAL + 短事务 + busy_timeout）与主 spec 无冲突。「排度器单写者」出自 `hub/queue_engine.py:4-5` 源码头注释——属实施时注释同步（见 C5b），非 spec 问题。
7. **tasks 覆盖度核对**：3 个 delta spec 共 9 requirement / 18 scenario，逐一对上 tasks 组 1-6；TDD 三测先行写法贯穿。

## 二、问题清单

### B 级（范畴错误/矛盾，实施前须修）

**B1：部署三步顺序矛盾——「chown → vault-import」会导致 PermissionError 卡死**
design Migration / tasks 6.1 / hub-deployment「部署文档可复核」scenario 三处均写「chown → vault-import → compose up」。矛盾：chown -R 1001:1001 之后，宿主部署用户（ubuntu, uid=1000）跑 `vault-import` 写 `<data_root>/vault/` 被 1001 属主挡死（这正是镜像契约收口复现过的同型权限问题）。正确序：**vault-import（data_root 尚属宿主用户，写入顺）→ chown -R 1001:1001（连 vault 一起递归）→ compose up**。
修法：三处顺序反转。

**B2：hub-deployment「常驻服务生命周期」正文「compose 中执行镜像 tag MUST 钉定显式版本」范畴错位，与同 spec D1 自相矛盾**
同 delta 前一 requirement 刚钉「执行容器 MUST NOT 出现在 compose service 定义中」——执行镜像根本不在 compose 里，无「compose 中执行镜像 tag」可言。钉定的真实载体是 F13 入队钉 tag（切片 1 已实现）+ 部署机显式 tag build + `image_exists` fail 暴露。
修法：该句改为「执行镜像版本钉定沿用 F13 入队钉 tag 契约（部署机显式 tag 预 build + hub image_exists 检查），不在 compose 层表达」。

**B3：SIGTERM 路由链缺失——「优雅退出」scenario 在容器形态下按文不可实现**
排度常驻 spec 钉「进程收到 SIGTERM/SIGINT SHALL 优雅退出：完成当前迭代后停止」。但 compose 形态下 PID 1 是 **uvicorn**（非 daemon 线程）：`docker stop` → SIGTERM → uvicorn → lifespan shutdown → 此时 `threading.Thread(daemon=True)` 随进程退出被**硬杀**，「完成当前迭代」不会发生（迭代若正在 docker CLI 调用中途被斩；靠 reconcile_startup 兜底能对账，但 spec 声明的优雅语义落空）。design D4 与 tasks 3.2/4.1 均未写信号到 stop_event 的路由。
修法：design D4 补路由链（lifespan shutdown 置 stop_event + join(timeout)）；tasks 4.1 补 shutdown 侧装配；tasks 5.2 compose 加 `stop_grace_period: 30s`（docker stop 默认 10s，覆盖一次慢迭代）。

### C 级（机械风险/低优先级）

**C4：execution-hub delta MODIFIED 标题与主 spec 原名不匹配**
主 spec 原名「单用户 env 直注 bootstrap」，delta MODIFIED 用新标题「单用户凭据来源（vault 优先 + env 兜底）」。validate 通过（宽松校验），archive sync 是 agent 驱动可理解改名意图，但机械稳妥欠佳。已实测：delta 顶部加 `## RENAMED Requirements`（FROM/TO）段后 validate 仍过。
修法：加 RENAMED 段标准表达（半页以内）。

**C5b：`hub/queue_engine.py:4-5` 头注释在 apply 时失真**
「单线程 tick 调用（排度器单写者）」在 daemon + API 双线程形态下措辞过时（tick 本身仍单循环串行，不变式保持；但「单写者」字面已被 D4 多线程短事务取代）。
修法：tasks 3.3 加半句「同步更新 queue_engine.py 头注释为 daemon 多线程事实」。

**C7：hub-deployment「崩溃自愈」scenario 无 compose 形态验证任务**
restart 拉起 + reconcile_startup 幂等有单测（3.1）与 compose 配置（5.2），但「compose 环境下 kill 一次观察自愈」无对应任务。
修法：tasks 6.2 冒烟措辞补「含一次 kill/重启自愈观察」。

## 三、裁决结果（用户 2026-10-21 批准）

B1-B3 全部落地修正 + C4/C5b/C7 随手带上——六项已全部修入产物：

- **B1**：design Migration / tasks 6.1 / hub-deployment「部署文档可复核」scenario 三处顺序反转为 vault-import → chown → compose up，并钉「顺序不可倒」理由（import 时 data_root 尚属宿主用户）。
- **B2**：hub-deployment「常驻服务生命周期」正文改为「执行镜像版本钉定沿用 F13 入队钉 tag 契约（部署机显式 tag 预 build + hub image_exists 检查 fail 暴露），不在 compose 层表达」。
- **B3**：design D4 补 SIGTERM 路由链（uvicorn lifespan shutdown 置 stop_event + join(timeout) + stop_grace_period: 30s）；tasks 4.1 补 shutdown 侧装配与 TDD、5.2 补 stop_grace_period。
- **C4**：execution-hub delta 顶部加 RENAMED 段（FROM「单用户 env 直注 bootstrap」TO「单用户凭据来源（vault 优先 + env 兜底）」），validate 复跑通过。
- **C5b**：tasks 3.3 补「同步更新 queue_engine.py 头注释『单线程 tick（排度器单写者）』为 daemon 多线程事实」。
- **C7**：tasks 6.2 冒烟补「含一次 kill/重启自愈观察——restart 拉起后 reconcile_startup 幂等对账实证」。
