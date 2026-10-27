# harden-verify-test-scope · design

## Context（事实基础）

- `run_daily.py` L3310-3312 docstring 钉双轨：Node 仓 `npm ci + npm test`；Python 仓重放 dev-agent 上报的 `test_cmd`；「测试归项目自治（ADR-0002）：dispatch 不替项目决定怎么测，只重放 dev-agent 上报的命令；未上报 → 跳过」。
- L3327-3330：`test_cmd_hint` 为空 → `note="dev-agent 未上报 test_cmd，跳过独立验证"`。
- dev 侧发布门（`evidence.py`）不校验命令范围——两层验证可共享同一窄命令面。

## 决策

### D1 优先序：profile `verify_test_cmd` > dev hint > 跳过

```python
# Python 轨（伪码）
test_cmd = (prof or {}).get("verify_test_cmd") or test_cmd_hint
if not test_cmd: skip(note="profile 与 dev 均未提供测试命令，跳过独立验证")
note 来源: "profile-declared" if prof 声明 else "dev-reported"
```

- profile 是**项目方 onboard 时自己写的**（`.project-auto` profiles，与 `match_surface` 等字段同层）——ADR-0002 的自治主体从「dev 上报」上移到「项目声明」，自治哲学不变、表达升级。
- dev hint 仍作回退：未 onboard verify_test_cmd 的既有项目零破坏（现状行为保留）。

### D2 Node 轨不动

`package.json` 的 `scripts.test` 就是项目测试门，`npm test` 天然全量——Node 轨无同盲问题。若个别 Node 项目想覆盖，未来再议（YAGNI）。

### D3 过时 profile 命令的失败方向是安全的

profile 命令过时 → 命令失败 `test_rc≠0` → 既有红路径（PR 标 failing、不合并）→ 人工更新 profile。fail-safe 方向：宁可假红拦下、不静默窄绿放过——与 fail-safe-dispatch「UNKNOWN 绝不当成功」同向。

### D4 与 verify-loop-discipline 的正交面

| 面 | verify-loop-discipline | 本变更 |
|---|---|---|
| test_rc 的角色 | B1：降为三维度审核的证实输入 | 保证该输入的范围独立性 |
| 失败三分 | B2：install 失败 = UNKNOWN 升人工 | 测试**命令选择**（install 之后） |
| requirement 交集 | pa-verify three-party review scope / Infrastructure-failure triage / PR lifecycle | Independent verification test scope（新） |

三者可在同一 capability 内共存，archive 时按各自 requirement 合并。

## Open Questions

1. `_post_merge_test_cmd`（L3386-，post-merge 测试）是否同用 profile 命令？倾向同改（同一全量语义），但 post-merge 面影响 merge 后动作，实施时确认后随手带上或留 follow-up。
