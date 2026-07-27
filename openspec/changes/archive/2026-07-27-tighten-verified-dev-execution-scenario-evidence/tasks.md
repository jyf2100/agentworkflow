# Tasks — tighten-verified-dev-execution-scenario-evidence

## 1. 规约修订

- [x] 1.1 据 2026-07-27 三专家回顾评审发现，重写 main spec `verified-dev-execution` 场景 4 THEN 子句对齐已交付证据：admitted 路径由确定性 pinned-SDK 集成测试直接回归锁定；denied 路径由共享 `can_use_tool` response-write 机制覆盖（通道保活与 admit/deny 分支无关，二者经同一 `transport.write`）；真实 dev-loop Node-command canary 显式延期至 dispatch 自然验证 — delta spec `## MODIFIED Requirements` 整段重写该 Requirement（前 3 场景不变）

## 2. 验证

- [x] 2.1 `openspec validate tighten-verified-dev-execution-scenario-evidence` 通过 — "Change is valid" (exit 0)
- [x] 2.2 纯文档变更：无代码/测试改动，既有 `bash scripts/quality.sh`（1243 passed, 6 xfailed）不受影响，无需重跑

## 3. 规约同步

- [x] 3.1 `/opsx:sync` delta → main spec — `openspec archive -y` 已执行 sync：`verified-dev-execution/spec.md` scenario 4 ~1 modified
- [x] 3.2 `/opsx:archive` — archived as `2026-07-27-tighten-verified-dev-execution-scenario-evidence`
