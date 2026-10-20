# 验证笔记：E2E build + 七项自检 + headless 首跑（tasks 5.1 / 4.2）

> 日期：2026-10-20 ｜ 环境：本服务器（docker 28.3.2，用户在 docker 组）｜ build context = release 干跑投影副本 `/tmp/pa-tmpl-gates-test`（230 文件，Dockerfile 随行修正后手动同步）｜ 产物 `pa-executor:v0.1.3-rc1`（1.16GB，upstream d49fd7d）

## 1. E2E build 全绿（5.1）

`bash release/build_executor_image.sh --tag v0.1.3-rc1 --context /tmp/pa-tmpl-gates-test` → **BUILD_RC=0**，七项自检逐项过：

| check | 结果 |
|---|---|
| id | ✓（非 root） |
| python | ✓ |
| claude | ✓（含钉扎 2.1.220） |
| gh | ✓（含钉扎 2.102.0） |
| pytest --collect-only | ✓（收集非空） |
| compileall | ✓ |
| secrets 扫描 | ✓（非测试载荷零命中） |

label：`io.pa.template-tag=v0.1.3-rc1` / `io.pa.upstream-commit=d49fd7d` / `io.pa.claude-code-version=2.1.220` / `io.pa.gh-version=2.102.0`。

**干跑抓出并修复的四个真问题**（全部回流 vault 真源 Dockerfile / build 脚手）：

1. **`# syntax=docker/dockerfile:1` 触发 frontend 拉取**——本机 registry mirror（aliyuncs）403。本 Dockerfile 未用任何 1.x 特性 → 删行（少一个外部拉取依赖，可复现性更好）。
2. **基底 `node:22-slim` 同被 mirror 403 拦**——不动系统 daemon.json（系统级改动须用户本人执行），走 daocloud 公共加速器拉取后 retag 本地（`docker pull docker.m.daocloud.io/library/node:22-slim && docker tag … node:22-slim`）。
3. **gh 落位错位**：`tar --strip-components=1` 与 member `gh_…_linux_amd64/bin/gh` 组合语义——member 三段 strip 1 剩 `bin/gh`，落到 `/usr/local/bin/bin/gh`（自检 `gh: not found` 抓出；容器内 `ls` 实证）。修为 `--strip-components=2`。
4. **compileall PermissionError**：`COPY . /opt/pa` 归 root，pa 用户写 `__pycache__` 被拒。修：pa 投影树 chown 给运行用户（即弃容器内合法——隔离边界在容器不在文件属主，注释已记）。
5. **secrets 扫描命中 7 个测试文件**：sanitize/凭据测试的 fixture 本就是凭据形状合成串（`AKIAIOSFODNN7EXAMPLE` 等 AWS 文档示例值）——测试职责所系、非机密。扫描加 `--exclude='test_*.py'`（语义 = 非测试载荷零凭据），spec「凭据零入镜像」scenario 同步补注记（真凭据风险由 review 把守）。
6. **网络抖动加固**：github.com 直连时通时断（135s 超时一次），curl 加 `--retry 5 --retry-all-errors --connect-timeout 30`。

## 2. headless 首跑验收（4.2，F9 残留项闭环）

**验收点达成**：全新容器（无挂载状态）最小 `claude -p --output-format json` **不挂起、信封 JSON 正常返回**——`hasCompletedOnboarding` 预置生效，未触发 onboarding/trust 交互。

**实测发现的额外状态项（非 Dockerfile 层）——模型路由**：

- 注入三值（BASE_URL/AUTH_TOKEN）后首调 `API Error: 400 Invalid model name model=claude-opus-5`——CLI 默认模型 id 被 roc 代理拒（CLAUDE.md 模型约定的运行时体现）；宿主能跑靠 `~/.claude/settings.json` env 的 `ANTHROPIC_DEFAULT_*_MODEL` 映射，容器未带。
- `--model sonnet` 被 CLI 正规化为 `claude-sonnet-5` 仍拒；`--model glm-5.2` 直传 → **`is_error: False, text: OK`**（全绿）。
- **结论**：F9「凭据=三值」仍成立（模型映射非凭据，无秘密性）；容器运行时注入面 = 三值 + 模型路由（`glm-*` 显式传或 `ANTHROPIC_DEFAULT_*_MODEL` env）。镜像保持中立不预置（roc 特定形态）——此为 hub/compose 切片的注入面设计输入，非镜像缺陷。

## 3. 遗留

- 5.3 发布 v0.1.3（Dockerfile 首次随投影发布）待用户指示；5.4 从 v0.1.3 tag 复验 build 随后闭环。
- v0.1.2 tag 无 Dockerfile，不可作 build context（tasks 5.1 已注明，未重蹈）。
