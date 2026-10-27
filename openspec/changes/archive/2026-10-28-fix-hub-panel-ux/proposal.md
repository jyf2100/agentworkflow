# Proposal: fix-hub-panel-ux

## Why

fix-hub-form-parity 落地面板建项目 forge 表单后（2026-10-27）做的 UX 审查发现三类问题，全部在 D1 形态（服务端渲染纯 HTML、零前端构建链）可修范围内：

1. **建项目表单收尾缺口（自相矛盾）**：fix-hub-form-parity 落地面板建项目 forge 表单后，提交页空下拉提示语仍写「先在服务器建必读面」——把用户往 ssh 引导，与面板自助建档的产品化意义（团队自助接入）相反；建项目成功消息只说「下拉已可见」，gitlab/gitcode 用户下一件事（去凭据页配 `GITLAB_TOKEN`/`GITCODE_TOKEN`，缺了派发前 fail-fast abandon）没有任何引导。
2. **最高优先真痛点：422 拒绝后表单字段值全部丢失**：填完 6 个字段错一个（如 gitlab_host 少 scheme），错误回显但所有输入清空、全部重填。提交 PRD / 建项目 / 凭据三张表单同病。
3. **可用性 / 可访问性债**：无 `<label>` 关联（读屏与点击聚焦不工作）；说明文字挤在 h2 标题里；输入框无 focus 可见态（键盘 tab 找不到焦点）；状态只靠颜色区分（色盲不友好）；journal `<pre>` 无高度上限、队列 `last_error` 不截断（长内容撑爆页面）；表格窄屏横向溢出；forge 选择与 gitlab_host 框无联动（选 github 时必填错觉）。

## What Changes

- **①收尾引导**：提交页空下拉提示改为链接到建项目页；建项目成功消息按 forge 形态给下一步引导（gitlab→凭据页配 `GITLAB_TOKEN`、gitcode→`GITCODE_TOKEN`、github→直接去提交）。
- **②表单回填**：render 函数族收已提交值参数，422 拒绝时把 form 值回填进 `value=`/`<textarea>`（经 `_esc` 唯一转义入口，无新攻击面）——三张表单（提交 PRD / 建项目 / 凭据）同修。凭据页 token 框不回填（密码语义，留空=保留既有值的契约不变）。
- **③表单结构**：逐字段 h2 换 `<label for=>` 关联 + `hint` 小字说明（说明从标题降为提示）；forge select 原生 inline `onchange` 联动 gitlab_host 框显隐（≤10 行、无外部资源、零依赖）。
- **④CSS 底线**：输入框/button `:focus` 可见态、button `:hover`、状态列加符号（`✓/✗/…` 不只靠颜色）、journal `<pre>` 高度上限 + 滚动、队列 `last_error` 截断（title 悬停全文）、表格容器横向滚动。

### 非 Goal

- 视觉重设计 / 主题系统 / 排版层次升级（质感批其余项：导航高亮、斑马纹、时间戳格式化——按需后续）
- JS 框架、CDN 资源、前端构建链、模板引擎（D1 红线不动）
- 表单客户端校验（校验真相源在服务端 `_reject`，客户端只做显隐联动）

## Capabilities

### Modified Capabilities

- `hub-panel`：`面板提交入口` requirement——空 project 清单引导从「部署侧建必读面」改为「界面建项目页链接」；`面板形态` requirement——补 UX 底线段（拒绝回填、label 关联、hint、focus 可见、状态非纯色、内联原生脚本边界、长内容滚动上限）
