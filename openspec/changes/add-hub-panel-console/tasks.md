# Tasks: add-hub-panel-console

- [x] 1. `hub/projects.py` 建档模块：`create_project(data_root, name, repo, goal, surface)`——
      name PROJECT_RE 校验、repo 规范化（owner/repo → https URL）、单行校验、双引号转义
      YAML 生成、失败清理；`ProjectSetupError` 明确报错不静默
- [x] 2. `hub/config.py`：`HUB_PANEL_ADMINS` env → `panel_admins: frozenset[str]`（空=回退态）
- [x] 3. `hub/panel.py`：`render_project_new`（表单+错误回显+成功态）+ 队列页行内操作
      （failed→重跑按钮 / pending→放弃按钮）+ 导航「建项目」链接
- [x] 4. `hub/api.py`：`GET /panel/projects/new`、`POST /panel/projects`、
      `POST /panel/queue/{id}/retry`、`POST /panel/queue/{id}/abandon`；
      `_require_admin` 守卫（admins 空=放行）；`_abandon_queue_item` 抽共享（REST DELETE 同源）
- [x] 5. 测试：test_projects.py（YAML 生成/转义/清理/重名/非法形态）+ test_api.py 增
      （建档 happy→提交页可见、admin 403、重跑 409 形态、放弃 409、未登录 401）
- [x] 6. `bash hub/quality.sh` 全绿
- [x] 7. 部署：compose 重建 + hub.env 补 `HUB_PANEL_ADMINS=rocju` + 真机实测
      （面板建 demo 项目 → 提交页清单出现）
- [ ] 8. spec sync + 归档（hub-panel ADDED 2 requirements）
- [x] 9. 面板 PRD 落盘即包装 frontmatter（2026-10-23 首跑 #4 实证修复：裸 md 被容器内
      pa inject 确定性拒绝；evidence 落 frontmatter.evidence 键可审计，不伪造
      source_path；deliver/REST 通道不包装）+ #4 已落盘文件一次性补插运维
