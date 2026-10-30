# 0009 — run 容器宿主可配：数据链靠共享数据面前提，非 hub 远程读回

> **关联：** 配套变更 openspec `add-hub-remote-runner`（design D4）；属 `execution-hub` 容器派发能力的宿主位置扩展。

## 决定

hub 可经 `HUB_DOCKER_HOST` 把 run 容器派发到远程 docker daemon（如 GPU 机）。容器在 daemon 机上经 bind mount 写 state（挂载源在 **daemon 侧**解析），而 hub 通知面 / 面板继续**本机直读** `HUB_DATA_ROOT`（pipeline_outcome / 报告 / dispatch record）——中间的数据链**不新增代码通道**，靠部署前提闭合：`HUB_RUNNER_MOUNT_ROOT`（daemon 侧路径）与 `HUB_DATA_ROOT`（hub 侧路径）双挂**同一存储**（NFS / 同盘）。

即：选「共享数据面是部署前提」而非「hub 远程读回是运行时保证」。

## 约束（这条决定的适用边界）

1. **前提双重时间性**：共享数据面须配置时对**且运行期持续有效**。运行中断链（NFS 掉挂/降级）**不 fail loud**——stamp 防撞（`allocate_fresh_stamp` 读 hub 侧树须能看见 daemon 侧新写入的 run 目录）依赖该前提，断链可能撞 stamp → 双 run 互踩 state 的静默竞争。故共享存储监控是部署前提的一部分，不是可选项。
2. **变更契约**：daemon 切换/回滚以**无在飞 run** 为前提（容器标识 = container id，是 daemon 相对的——切换后 status 查无 → 孤儿判定 → 重派 → 双机双跑）；`HUB_RUNNER_MOUNT_ROOT` 是派发时投影参数，变更即时生效、无队列契约（队列项 `prd_path` 恒存 hub 视角，派发时投影为挂载视角，见 change design D2 投影模型——apply 期修正：原「入队固化 daemon 视角」与 `prd_path` 的 hub 侧 5 处消费及 POST /queue / CLI / 重跑三外部通道冲突）。
3. **配置期误配的 fail 机理**：dockerd 对缺失挂载源自动建目录，fail 点后移至容器内 pa 运行时（读不到 PRD / state 断链）→ run failed 可见，但不是「容器启动失败」。真正的防 drift 防线 = 确定性 hash 文件名（`mm-<sha8>.md`）+ 文件级挂载——该确定性同时是 `deliver_key` 幂等的前提，**双重依赖**：文件名形态的任何改动须同时审幂等与远程完整性两面。
4. **零变化不变式**：两 env 不设 = 现状 byte-identical（本决定的全部代码面都在 opt-in 配置后）。

## 背景

需求：run 容器跑在指定机器（GPU 机 183 带 nvidia runtime；执行负载从 hub 宿主机剥离）。`hub/runner.py` 全走 docker CLI，`DOCKER_HOST` env 原生路由远程 daemon。远程化三断点：① bind mount 挂载源在 daemon 侧解析；② hub 本机读工件的数据链；③ 镜像须在目标 daemon 存在。①靠挂载源根可配 + 前提闭合（本 ADR）；③靠预载声明 + 派发时 `start()` 首步 `image_exists` 复查（F13 既有语义，时间窗自愈）；②是本 ADR 的靶心。

## 考虑过的替代

- **hub 经 docker API / `docker cp` 远程读回工件** —— 拒绝：通知面/面板读路径遍布 `data_root`（pipeline_outcome / 报告落卷 / dispatch record），逐点改造面大；且通知面可用性从此绑死 hub→daemon 网络链。
- **每 run 打包回传（容器退出时 tar state 推回 hub）** —— 拒绝：写面在容器内 pa（`run_daily`），其不知 hub 位置；大 state 传输成本与退出路径复杂度不成比例。
- **不做远程（保本机 socket）** —— 拒绝：GPU 负载与执行负载剥离的需求真实存在；docker CLI 抽象使 daemon 位置对本近乎透明，改造成本主要落在前提声明而非代码。
