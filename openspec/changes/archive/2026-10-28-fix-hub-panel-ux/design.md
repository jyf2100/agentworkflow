# Design: fix-hub-panel-ux

## Context

面板 = `hub/panel.py` 单文件 render 函数族 + 一段内联 `_STYLE`（~15 行 CSS），api.py 挂端点、`_reject` 渲染 422 回显。fix-hub-form-parity（面板建项目表单 forge 三形态增补，尚未归档——本 change 归档在其后）刚给建项目表单补 forge 选择（value=""=github 缺省 / gitlab / gitcode），但表现层停留在这三类的功能形态。UX 审查（2026-10-27，提案所列三类问题）要求在 D1 形态内补表现层底线。

## Goals

- 422 拒绝零重填；表单可访问（label/hint/focus）；状态与长内容可读；建项目界面引导闭环（空下拉→建项目页→成功→凭据页）。
- D1 红线不动：零新依赖、零外部资源、f-string 渲染、`_esc` 唯一转义入口。

## 决策

### D1 表单回填走服务端渲染（非客户端状态）

render 函数族收可选已提交值（`values: dict | None`），`_reject` 时把 form 值透传回填 `value="{_esc(v)}"` / textarea 正文 / select `selected`。不引 JS、不引 cookie/localStorage。安全面：回填值与错误消息同路径——只经 `_esc` 进属性/文本，防线唯一性不动。**凭据页 token/password 框例外不回填**（密码语义 + 「留空=保留既有值」契约；base_url/model 等非敏感框照常回填）。

### D2 原生 inline JS 的边界：显隐联动允许，其余仍禁

主 spec「面板形态」措辞是 MUST NOT 引入「前端构建链、JS 框架或额外模板引擎依赖」。forge→gitlab_host 显隐用原生 `onchange` inline handler（≤10 行、无外部资源、无依赖）：禁 JS 时不联动但 gitlab_host 框仍显示且 hint 写明「仅 forge=gitlab 必填」——降级可接受，语义校验在服务端（缺 host 照样 422）。spec 细化为：内联原生脚本 SHALL 限于表单控件显隐联动，MUST NOT 引入外部脚本资源或 JS 框架。

### D3 字段结构：label + hint 替代逐字段 h2

`<label for="f-name">项目名</label>` + `<p class="hint">小写字母/数字/-/_</p>`；h2 只留节级分组（如「平台 token」）。id 前缀 `f-` 命名空间化。hint 进 `_STYLE`（`.hint{…small/dim}`）。

### D4 状态符号映射（非纯色）

done/succeeded → `✓`、failed/failed_to_start → `✗`、其余（pending/running/…）→ `…`，与既有 `_status_class` 色并行（符号+色双通道，色盲可辨）。

### D5 长内容有界

journal `<pre>` 加 `max-height + overflow:auto`；队列 `last_error` CSS 截断（`max-width` + `text-overflow:ellipsis` + `title` 属性悬停全文——title 值经 `_esc`）；表格包 `overflow-x:auto` 容器（窄屏）。

## Risks / Trade-offs

- **回填值含引号/HTML**：经 `_esc` 进属性，与错误消息同防线——已有测试模式（换行注入拒绝）覆盖，不新增攻击面。
- **JS 被禁**：联动失效但功能不损（gitlab_host 常显 + hint 说明 + 服务端校验兜底）。
- **spec 措辞放宽（inline JS）**：边界写死「仅显隐联动、无外部资源」，防止滑坡成脚本堆积。

## Migration Plan

1. 收尾引导（A1/A2）→ 2. 回填（D1）+ 测试 → 3. label/hint（D3）+ forge 联动（D2）→ 4. CSS 底线（D4/D5 + focus/hover）→ 5. 质量门 + 真机 rebuild 面板验证。测试面沿用 test_api/test_panel 既有模式加断言：422 页含回填值 / label for-id 对 / hint 存在 / onchange 联动静态断言 / focus CSS 存在。
