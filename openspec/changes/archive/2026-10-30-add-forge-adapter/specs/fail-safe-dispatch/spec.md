# Delta Spec: fail-safe-dispatch（修改）

## MODIFIED Requirements

### Requirement: Explicit external lookup outcome
Every dispatch-critical Git-hosting-platform or Git remote lookup MUST return an explicit `FOUND`, `NOT_FOUND`, or `UNKNOWN` outcome with diagnostic context. 平台覆盖由 forge-adapter capability 声明（github / gitlab / gitcode）；三态判据是 adapter 契约的一部分，dispatch 层 MUST NOT 绕过 adapter 自行解析平台响应。REST 分支的配置类确定性失败（HTTP 401/403）不属三态：dispatch 消费面 MUST 将其映射为单项目终态（skip + 高声暴露），不进瞬态重试臂、不影响同批其他项目。

#### Scenario: Valid empty query response
- **WHEN** the external command or API call succeeds and returns a valid empty result
- **THEN** the lookup returns `FOUND` with an empty result; dispatch consumes FOUND-empty per call-site semantics（`_lookup_pr` 空 = 无既有 PR，按无 PR 处理；`count_inflight_prs` 空 = 0 在途）——映射按调用点既有规则完成，adapter 恒返回 FOUND-空、不感知调用语境

#### Scenario: External command cannot establish state
- **WHEN** the command or API call times out, exits non-zero or returns non-2xx, lacks authentication（gh 形态凭证缺失）, is rate-limited (429), is unavailable, or returns invalid data
- **THEN** the lookup returns `UNKNOWN` and records a sanitized reason（REST 分支的 401/403 除外——按上方配置类失败条款处理）

#### Scenario: REST 认证失败是单项目终态而非 UNKNOWN
- **WHEN** gitlab/gitcode 调用收到 HTTP 401/403（token 无效 / 权限不足）
- **THEN** 该项目按配置类确定性失败 skip + 高声暴露（不重试、不炸整个 dispatch 段）；gh 分支的凭证缺失仍走 `UNKNOWN`

#### Scenario: Non-GitHub platform gains identical guarantees
- **WHEN** the target repository is hosted on gitlab (local instance) or gitcode
- **THEN** every dispatch-critical remote lookup returns the same explicit three-outcome contract with the same fail-safe semantics as the github path (UNKNOWN never treated as success)
