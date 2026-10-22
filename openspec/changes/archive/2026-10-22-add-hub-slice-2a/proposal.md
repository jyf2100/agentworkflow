## Why

切片 1（add-hub-skeleton）交付的是**被动骨架**：lifecycle 的 tick/reconcile 靠测试手动驱动、凭据靠 hub 进程 env 直注（无持久化保险箱）、部署无编排（手工拼 docker 命令 + 2026-10-21 镜像契约收口实测暴露镜像运行用户 `pa(uid=1001)` 与宿主卷属主不对齐会直接 PermissionError）。hub 要成为团队服务器上**可常驻运行**的执行面服务，这三块是缺口；切片 2 经 grilling 裁决拆为 2a（保险箱+排度常驻+compose，零身份系统依赖）与 2b（OAuth 身份），2a 可即刻开工零悬空。

## What Changes

- **凭据保险箱**：per-user 凭据加密落盘（3 值 `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`GH_TOKEN` + 可选模型路由，F9 及切片 0 E2E 实测补记的注入面）；替代切片 1「hub 进程 env 直注」来源，容器注入通道不变（`--env-file` 0600 即弃即删，D6）。单用户形态：保险箱首条记录由 bootstrap 命令从 env/交互录入。
- **排度常驻服务**：lifecycle 循环 daemon 化——`tick_dispatch` + `reconcile_running` + `reconcile_startup`（重启时）按可配间隔循环，信号优雅退出（SIGTERM 排空当前 tick 不中断容器）。
- **compose 部署**：hub 服务 + data 卷布局 + `pa-executor` tag 钉定的 docker compose 描述；**宿主卷属主对齐**（镜像 `pa(uid=1001)` 须可写 state 卷——实测 PermissionError 的修复面）；dinD/socket 挂载（hub 起兄弟容器而非容器套容器）。
- **单用户形态延续**：`enqueued_by` 兜底 `cfg.user_id` 不变；OAuth/Mattermost 身份面全部在 2b。

## Capabilities

### New Capabilities
- `hub-credential-vault`: hub 侧 per-user 凭据加密存储与注入面契约（3 值 + 可选模型路由；明文只在内存与 0600 env-file 短暂存在）。
- `hub-deployment`: hub 的 compose 部署契约（服务编排、卷布局与属主对齐、镜像版本钉定、常驻服务的生命周期管理）。

### Modified Capabilities
- `execution-hub`: 「单用户 env 直注 bootstrap」requirement 改为「凭据来源 = 保险箱（单用户过渡形态 env 兜底保留）」；新增「排度常驻服务」requirement（tick/reconcile 循环 + 优雅退出 + 重启对账时序）。

## Impact

- **代码**：`hub/` 新增 vault 存储模块 + daemon 入口；`lifecycle.py`/`config.py` 凭据来源改造；新增 compose 文件与部署文档（RUNBOOK 新节）。
- **契约**：容器注入面零变化（仍 `--env-file` 三值+路由）；state 卷写面零变化；`RunSpec` 不变。
- **依赖**：加密原语走 Python 标准库（`cryptography` 若需引入则评估 cron 依赖极薄原则——hub 非 cron 环境，约束不同）；compose 为部署机新增文件，不影响开发 checkout。
- **不在本切片**：OAuth/身份（2b）、投递 API（F11，切片 3）、Mattermost bot（切片 4）、镜像 build 自动化（F13 已裁手动）。
