# harden-verify-test-scope · tasks

## 1. independent_verify 优先序（run_daily.py）

- [ ] 1.1 写测试（先红）：Python 轨 profile 声明 `verify_test_cmd` + dev 上报 hint → 用 profile 命令，note=`profile-declared`
- [ ] 1.2 写测试（先红）：无声明 + dev hint → 重放 hint，note=`dev-reported`（现状回归锚）
- [ ] 1.3 写测试（先红）：皆无 → 跳过 + note（现状回归锚）
- [ ] 1.4 写测试（先红）：Node 轨（package.json 存在）→ `npm ci + npm test` 不受 profile/hint 影响（回归锚）
- [ ] 1.5 实现：`independent_verify` Python 轨优先序 + note 来源标注（design D1 伪码，约 15 行）；`_post_merge_test_cmd` 按 design Open Question 1 实测确认后同改或留 follow-up（结案回写 design）

## 2. 接线与文档

- [ ] 2.1 dispatch 调用点把 `prof` 透传进 `independent_verify` 既有 `prof` 参数位（L3309 签名已含 `prof: dict | None`——核对现有调用是否已传，未传则补）
- [ ] 2.2 `graph_pa`* 对应 node 同步（直调面同改；flag off 不影响）
- [ ] 2.3 `CONTEXT.md` profile 字段表补 `verify_test_cmd`；`RUNBOOK.md` 项目 onboard 节补一句「建议声明全量测试命令以堵独立验证同盲」
- [ ] 2.4 全量 `bash scripts/quality.sh` 绿；`openspec validate harden-verify-test-scope` 过
