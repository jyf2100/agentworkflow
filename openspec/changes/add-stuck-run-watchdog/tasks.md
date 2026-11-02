# 任务：add-stuck-run-watchdog

## 1. 配置面（HubConfig）

- [x] 1.1 `config.py` 增五键：`HUB_STUCK_TICKS`(默认 36) / `HUB_STUCK_AFTER_MINUTES`(默认 120) / `HUB_STUCK_KILL`(默认 0) / `HUB_STUCK_KILL_GRACE_MINUTES`(默认 30) / `HUB_STUCK_KILL_FLOOR_MINUTES`(默认 None)；校验同 stale/probe 同型——非正数 / kill 非 0|1 → ConfigError；**kill=1 而 FLOOR 未配 → ConfigError**（未标定禁开 kill，配置错必须响）
  - 实证：config.py 五键 + 跨键断言已落；`test_config.py::test_stuck_kill_requires_floor_fail_fast` 等四用例绿
- [x] 1.2 单测：缺省零行为（检测开、告警档、kill 关）；非法值拒起逐键覆盖
  - 实证：`test_config.py::test_stuck_keys_defaults` / `test_stuck_invalid_values_rejected` / `test_stuck_kill_only_accepts_0_or_1` 绿

## 2. 检测模块（hub/stuck_check.py）

- [x] 2.1 监督面 mtime 判据：**stdout tee `<stamp>.stdout.log`（主面，stamp = run_dir.name 前缀）** + 平铺 events（`events.locate_pa_artifact` 既有寻址），取 max(mtime)；全缺 → 不判 stuck（failed_to_start 归 reconcile）；journal 面移除（真机实证：`journal_driven_dispatch` flag 生产关闭，124+ run 零 journal——2026-11-02 #137–#139 现场核查）；pa 侧摘要命名的 artifacts/reports 面与 worktree 侧遥测不入面（`ids.run_id` = 内容摘要 hub 不可重建）；clock/stat 注入接缝（fake clock 测试）
  - 实证：`supervision_mtime`（now_fn 注入；单面 stat 异常吞为缺席）；两轮实施期更正均已同步 spec/design/proposal/tasks 四文件
- [x] 2.2 去抖与消解：进程内 set 键 `(run_id, 档)` 三档 `alerted` / `kill_done` / `kill_failed`；告警一次 → mtime 前进消解回执清键 → 自然终态终态回执清键；grace 按「mtime 龄 ≥ max(AFTER+GRACE, FLOOR)」一次度量（无时间戳键，design D5 等价关系）；重启丢失最多重告一次（stale_check 同款）
  - 实证：`check_stuck_runs` 离场清理循环 + `_supervise_one` 消解分支；`test_departure_sends_terminal_receipt_and_clears_all_tiers`（kill_failed 档静默清断言）/ `test_recovery_sends_resolution_receipt_and_clears` 绿
- [x] 2.3 告警与回执面：`notify_admin`（run 号 / 项目 / 停写时长 / 「串行名额被占」提示 / 人工处置指针；kill 回执含「终态归对账」；终态回执闭环已告警 run）；文本零凭据零 env 值（值不入面）；通道 fail-open
  - 实证：notify_admin 内部 fail-open（notify.py:319 既有）；告警/回执文案全机械字段无 env 值；`test_stale_triggers_alert_once_debounced` 断言文案内容
- [x] 2.4 单测：超阈值检出告警 / 未达阈值（含单 dev 会话中段 100min < 120）零行为 / 恢复消解回执 / 自然终态回执 / 去抖不重复 / journal+events 双缺不误报 / events stamp 翻倍 glob 兜底命中 / 单 run stat 异常不连坐
  - 实证：test_stuck_check.py 16 用例全绿（含 `test_mtime_double_stamp_glob_fallback_hits` 双前缀兜底、`test_per_run_exception_no_crosstalk`）

## 3. kill 通道（flag 默认关 + FLOOR 标定前置）

- [x] 3.0 `runner.py` FakeRunner.stop 语义对齐：→ `exited` + `exit_code=137`（与 DockerRunner.stop 行为一致；r1 B2——kill 是 `Runner.stop` 首个生产调用点，测试替身先行对齐）
  - 实证：runner.py:142 对齐；受影响用例同步修——`test_runner.py::test_fake_stop_exited_137_and_terminal_idempotent`（改名重写）、`test_lifecycle.py` 两处 gone 模拟改直置 state（stop 不再产 gone）
- [x] 3.1 判据链：kill=1 + FLOOR 已配 + mtime 龄 ≥ max(AFTER+GRACE, FLOOR) + **开工前 mtime 复测**（赛跑安全——已恢复则放弃 kill 只发消解回执；复测对 hub 观测面无鉴别力，防误杀主力是 FLOOR 门，design D2）
  - 实证：`_supervise_one` kill 段；`test_kill_recheck_recovered_aborts_with_receipt`（supervision_mtime 两次读数 monkeypatch）绿
- [x] 3.2 kill 动作 = `Runner.stop(container_ref, docker_host=per-run 快照)`；成功 → kill 回执 + 键 kill_done；**绝不直接调 `_finish_run`/`on_exit`**——终态归 reconcile 下轮 exited 分支（单写者纪律）
  - 实证：`test_kill_routes_per_run_docker_host_snapshot`（host 快照路由断言）+ `test_stuck_kill_then_reconcile_archives_failed_and_redispatches` 集成（exited 137 → failed → item pending 重派）绿
- [x] 3.3 kill 失败闩：stop 抛异常 → 升级告警一条 + 键 `kill_failed`（进程生命周期不再重试，否则 3min 槽距构成重试轰炸）；闩随消解/run 离场清
  - 实证：`test_stop_failure_latches_no_retry_bombardment`（三槽零重试 + 升级告警恰一条）绿
- [x] 3.4 单测：flag 关零 stop / kill=1 无 FLOOR 拒起 / 未满 max(AFTER+GRACE, FLOOR) 零 stop / 双窗满 + 复测仍停写 → 恰一次 stop + 正确 docker_host + kill 回执 / 复测已恢复零 stop / stop 异常 → 升级告警恰一条且后续槽**零重试**（闩断言）/ kill 后 reconcile exited 分支归 failed 走 on_exit（集成断言）
  - 实证：上述 kill 族五用例 + config 拒起 + lifecycle 集成，全绿

## 4. 挂点（daemon + lifecycle）

- [x] 4.1 `Lifecycle.check_stuck_runs()` 薄挂点（probe_runners 同构）：取 `runs_in_status("running")` → 逐 run `Runner.status` 双重事实过滤 → stuck_check 判定；per-run 异常隔离；顺带对已离场 run 清键 + 终态回执
  - 实证：lifecycle.py `check_stuck_runs` + `self._stuck_seen`（__init__ 持有）；`test_check_stuck_runs_thin_hook_assembles_deps` 装配断言绿
- [x] 4.2 `daemon.py` 第八低频槽（现状已有七个：stale/probe/drift/digest/events/relay/observer）：`tick % stuck_ticks == 0` 触发，try/except 兜底记日志续跑（M1 防御纵深同型）
  - 实证：daemon.py 第八槽 `[stuck][warn]` 兜底；`test_stuck_check_runs_every_n_ticks` / `test_stuck_check_exception_does_not_kill_loop` 绿
- [x] 4.3 单测：daemon 槽触发节奏（fake tick 计数）/ 槽内异常不崩循环 / lifecycle 挂点对 DB running 但容器 exited 的 run 零行为（交 reconcile）
  - 实证：daemon 两用例 + `test_container_exited_zero_behavior`（stuck_check 层）绿

## 5. 文档与部署

- [x] 5.1 `hub.env.example` 五键注释样例（kill 键粗体声明破坏性默认关 + FLOOR 标定前置说明；AFTER 缺省语义 = 可见性非精确判定，标定后校准）
  - 实证：hub.env.example 末「卡死 run 带外墙钟监督」节已落（含启用前置与回滚行）
- [x] 5.2 RUNBOOK 新节「卡死 run 看门狗」：监督面构成与判据 / 告警样例与人工处置（现行 `docker stop` 人工路径降级为兜底说明）/ kill 启用步骤（7.1 标定 → 写 FLOOR → 开 flag → 重启）与回滚（关 flag 重启；stop 后对账全自动）/ 跨机 NFS attribute cache 盲窗边界（D7）
  - 实证：RUNBOOK.md §28 已落（含人工处置「不要手改 DB 终态」与跨机盲窗行）

## 6. 质量门

- [x] 6.1 `bash hub/quality.sh` 全绿（compileall + pytest + ruff，新单测并入）
  - 实证：2026-11-02 本机实跑 rc=0——**514 passed**（新增 stuck 族 24 用例）+ compileall + ruff E9/F 全绿；首跑曾抓 ruff F841 未用变量（test_lifecycle）当场修
- [x] 6.2 `openspec validate add-stuck-run-watchdog --strict --no-interactive` 通过
  - 实证：rc=0 valid；全库 `validate --all --strict` 53/53

## 7. 真机验证

- [ ] 7.1 **阈值标定（kill 启用前置门）**：kill 关档跑真机，采集单 dev SDK 会话时长分布（hub 可见面静默实测），标定 FLOOR = 实测上限写入部署配置；告警阈值 AFTER 按误报率校准——FLOOR 未标定前 kill 保持关闭（配置面已机械强制）
  - 部分实证（2026-11-02 真机，run #139 全程观察）：健康(近)会话的 hub 可见静默实测达 **50+ 分钟**（dev 单会话 263 turn、3600s wall-clock 超时收场）——120min AFTER 缺省在整个观察窗内零误报；跨机形态（runner-pool 派 183）下 stdout face 经 NFS 双挂可读、监督有效（D7 的 attribute cache 延迟未构成问题）。FLOOR 标定仍欠长会话样本，未写入部署配置
- [ ] 7.2 受控停摆全链（本机 + 跨机各一轮）：构造真停摆（容器内挂起 / 断 LLM 网络）→ 告警到达 → kill 开档双窗后 stop（`DockerRunner.stop` 首次真机使用）→ kill 回执 → reconcile 归 failed → on_exit 重派状态机接管；跨机轮含 NFS attribute cache 复测盲窗观察（D7）——**标注：模块就绪待真机，不虚报完成**
  - **检测半链已真机验证 ✓**（2026-11-02 合成演练）：假 running 行 + 陈旧 stdout 面（3h）→ stuck 槽 ≤3 分钟检出告警（`[stuck][warn]` + notify_admin DM 精确含 run 号/停写时长/名额提示/RUNBOOK 指针）→ 去抖恰一次 → kill 关档零 stop → 行离场后闭环回执。**kill 半链未真机验证**（FLOOR 未标定，配置面禁止开）——`DockerRunner.stop` 首次真机使用与 kill 回执待 7.1 标定后补；集成测试已覆盖（test_lifecycle kill→exited 137→failed→重派）
