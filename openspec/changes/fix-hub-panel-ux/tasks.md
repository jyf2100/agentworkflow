# Tasks: fix-hub-panel-ux

## 1. 建项目表单收尾引导

- [x] 1.1 `hub/panel.py render_submit`：空 project 清单提示改为链接 `/panel/projects/new`（「先去建项目」），删除「先在服务器建必读面」ssh 引导文案
- [x] 1.2 `hub/api.py panel_project_create` 成功消息按 forge 形态给下一步引导：gitlab→凭据页配 `GITLAB_TOKEN`、gitcode→`GITCODE_TOKEN`、github→直接提交页；消息含 `/panel/credentials` 链接（forge 非 github 时）

## 2. 表单回填（三表单）

- [x] 2.1 `hub/panel.py` 三张表单 render 函数收可选 `values: dict | None`，字段回填 `value="{_esc}"` / textarea 正文 / select `selected`；`hub/api.py` 三个 `_reject` 路径透传 form 值；凭据页 token/password 框不回填（base_url/model 回填）
- [x] 2.2 测试（`hub/test_api.py`）：建项目 422 页含已填 name/repo/goal/surface 值；提交 PRD 422（evidence 空）页含已贴 PRD 正文；凭据 422 页 token 框不含已填值（password 例外锚）

## 3. 表单结构（label/hint）+ forge 联动

- [x] 3.1 `hub/panel.py` 三张表单逐字段 h2 换 `<label for="f-…">` + `<p class="hint">`（说明从标题降为提示）；`.hint` 进 `_STYLE`；节级 h2 保留分组
- [x] 3.2 建项目表单 forge select 加原生 inline `onchange` 联动 gitlab_host 框显隐（≤10 行、无外部资源）；hint 写明「仅 forge=gitlab 必填」保证禁 JS 降级可读
- [x] 3.3 测试：label for-id 配对存在 / hint 存在 / onchange 属性静态断言（`hub/test_panel.py` 或 test_api）

## 4. CSS 底线

- [x] 4.1 `_STYLE` 补：输入框与 button `:focus` 可见态、button `:hover`、状态符号（D4 映射 `✓/✗/…`，`_status_class` 旁加符号函数）、journal `pre` 高度上限 + 滚动、`last_error` 截断（`text-overflow:ellipsis` + `title` 悬停全文）、表格容器 `overflow-x:auto`
- [x] 4.2 测试：状态符号出现在队列/run 渲染输出（test_panel 既有模式加断言）

## 5. 质量门 + 真机

- [x] 5.1 `bash hub/quality.sh` 全绿（从仓库根）
- [x] 5.2 184 rebuild + force-recreate 后真机面板验证：422 回填可见、forge 联动、状态符号、收尾引导链接（会话 token 容器内签名流式传递，不进对话）
