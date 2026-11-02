# 任务：add-stuck-run-watchdog

## 1. 配置面（HubConfig）

- [ ] 1.1 `config.py` 增五键：`HUB_STUCK_TICKS`(默认 36) / `HUB_STUCK_AFTER_MINUTES`(默认 120) / `HUB_STUCK_KILL`(默认 0) / `HUB_STUCK_KILL_GRACE_MINUTES`(默认 30) / `HUB_STUCK_KILL_FLOOR_MINUTES`(默认 None)；校验同 stale/probe 同型——非正数 / kill 非 0|1 → ConfigError；**kill=1 而 FLOOR 未配 → ConfigError**（未标定禁开 kill，配置错必须响）
- [ ] 1.2 单测：缺省零行为（检测开、告警档、kill 关）；非法值拒起逐键覆盖；kill=1 无 FLOOR 拒起专项

## 2. 检测模块（hub/stuck_check.py）

- [ ] 2.1 监督面 mtime 判据：平铺 journal + 平铺 events（经 `events.locate_pa_artifact` 既有寻址，二者为 run_dir 平铺兄弟文件）+ `state/artifacts/<stamp>_<slug>/` 前缀树 + `state/reports/` 同前缀文件，取 max(mtime)；共享树 MUST 按 stamp_slug 前缀过滤（复用 locate_pa_artifact 的 stamp 前缀事实）；journal 与 events 均缺失 → 不判 stuck（failed_to_start 归 reconcile）；worktree 侧 run_log / handoff-latest 不入面；clock/stat 注入接缝（fake clock 测试）
- [ ] 2.2 去抖与消解：进程内 set 键 `(run_id, 档)` 三档 `alerted` / `kill_done` / `kill_failed`；告警一次 → mtime 前进消解回执清键 → 自然终态终态回执清键；grace 按「mtime 龄 ≥ max(AFTER+GRACE, FLOOR)」一次度量（无时间戳键，design D5 等价关系）；重启丢失最多重告一次（stale_check 同款）
- [ ] 2.3 告警与回执面：`notify_admin`（run 号 / 项目 / 停写时长 / 「串行名额被占」提示 / 人工处置指针；kill 回执含「终态归对账」；终态回执闭环已告警 run）；文本零凭据零 env 值（值不入面）；通道 fail-open
- [ ] 2.4 单测：超阈值检出告警 / 未达阈值（含单 dev 会话中段 100min < 120）零行为 / 恢复消解回执 / 自然终态回执 / 去抖不重复 / journal+events 双缺不误报 / 共享树前缀过滤不串扰 / 单 run stat 异常不连坐

## 3. kill 通道（flag 默认关 + FLOOR 标定前置）

- [ ] 3.0 `runner.py` FakeRunner.stop 语义对齐：→ `exited` + `exit_code=137`（与 DockerRunner.stop 行为一致；r1 B2——kill 是 `Runner.stop` 首个生产调用点，测试替身先行对齐）
- [ ] 3.1 判据链：kill=1 + FLOOR 已配 + mtime 龄 ≥ max(AFTER+GRACE, FLOOR) + **开工前 mtime 复测**（赛跑安全——已恢复则放弃 kill 只发消解回执；复测对 hub 观测面无鉴别力，防误杀主力是 FLOOR 门，design D2）
- [ ] 3.2 kill 动作 = `Runner.stop(container_ref, docker_host=per-run 快照)`；成功 → kill 回执 + 键 kill_done；**绝不直接调 `_finish_run`/`on_exit`**——终态归 reconcile 下轮 exited 分支（单写者纪律）
- [ ] 3.3 kill 失败闩：stop 抛异常 → 升级告警一条 + 键 `kill_failed`（进程生命周期不再重试，否则 3min 槽距构成重试轰炸）；闩随消解/run 离场清
- [ ] 3.4 单测：flag 关零 stop / kill=1 无 FLOOR 拒起 / 未满 max(AFTER+GRACE, FLOOR) 零 stop / 双窗满 + 复测仍停写 → 恰一次 stop + 正确 docker_host + kill 回执 / 复测已恢复零 stop / stop 异常 → 升级告警恰一条且后续槽**零重试**（闩断言）/ kill 后 reconcile exited 分支归 failed 走 on_exit（集成断言）

## 4. 挂点（daemon + lifecycle）

- [ ] 4.1 `Lifecycle.check_stuck_runs()` 薄挂点（probe_runners 同构）：取 `runs_in_status("running")` → 逐 run `Runner.status` 双重事实过滤 → stuck_check 判定；per-run 异常隔离；顺带对已离场 run 清键 + 终态回执
- [ ] 4.2 `daemon.py` 第八低频槽（现状已有七个：stale/probe/drift/digest/events/relay/observer）：`tick % stuck_ticks == 0` 触发，try/except 兜底记日志续跑（M1 防御纵深同型）
- [ ] 4.3 单测：daemon 槽触发节奏（fake tick 计数）/ 槽内异常不崩循环 / lifecycle 挂点对 DB running 但容器 exited 的 run 零行为（交 reconcile）

## 5. 文档与部署

- [ ] 5.1 `hub.env.example` 五键注释样例（kill 键粗体声明破坏性默认关 + FLOOR 标定前置说明；AFTER 缺省语义 = 可见性非精确判定，标定后校准）
- [ ] 5.2 RUNBOOK 新节「卡死 run 看门狗」：监督面构成与判据 / 告警样例与人工处置（现行 `docker stop` 人工路径降级为兜底说明）/ kill 启用步骤（7.1 标定 → 写 FLOOR → 开 flag → 重启）与回滚（关 flag 重启；stop 后对账全自动）/ 跨机 NFS attribute cache 盲窗边界（D7）

## 6. 质量门

- [ ] 6.1 `bash hub/quality.sh` 全绿（compileall + pytest + ruff，新单测并入）
- [ ] 6.2 `openspec validate add-stuck-run-watchdog --strict --no-interactive` 通过

## 7. 真机验证

- [ ] 7.1 **阈值标定（kill 启用前置门）**：kill 关档跑真机，采集单 dev SDK 会话时长分布（hub 可见面静默实测），标定 FLOOR = 实测上限写入部署配置；告警阈值 AFTER 按误报率校准——FLOOR 未标定前 kill 保持关闭（配置面已机械强制）
- [ ] 7.2 受控停摆全链（本机 + 跨机各一轮）：构造真停摆（容器内挂起 / 断 LLM 网络）→ 告警到达 → kill 开档双窗后 stop（`DockerRunner.stop` 首次真机使用）→ kill 回执 → reconcile 归 failed → on_exit 重派状态机接管；跨机轮含 NFS attribute cache 复测盲窗观察（D7）——**标注：模块就绪待真机，不虚报完成**
