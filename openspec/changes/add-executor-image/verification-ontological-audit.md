# 验证笔记：本体论全方位审核（实现全集 @ 12/14 任务后）

> 日期：2026-10-20 ｜ 审核面：实现（check_release_gates / build_executor_image / Dockerfile / release 接线 / 测试×2 / container_sandbox 迁移 / CLAUDE.template.md）× change artifacts（proposal / design D1-D10 / delta specs×2）× 主 specs（template-release / executor-image / isolated-observable-execution）× 阶段 2 design doc（server-deployment）｜ 方法：实体同一性 / 范畴归属 / 关系不变式 / 不变式相容性四维 + F 编号发现 + 用户裁决

## 1. 发现与裁决总表

| # | 级别 | 发现 | 裁决 | 落点 |
|---|---|---|---|---|
| F1 | 高 | spec「build context 混入投影外文件 → build 失败」scenario 无实现，且「内容清单」真源（manifest）不随投影——context 侧无判据，spec 承诺了机械上无法执行的校验（本体空缺） | **方案 b**（用户）：git checkout 场景 ls-files 差集校验，等价导出告警跳过 | build_executor_image.sh verify 段 + test_build_executor_image.py ×2 用例 + executor-image delta scenario 实现语义注记 |
| F2 | 中 | F12 差集 warning 不阻断 = 防线自愿豁免：维护者忽略警告 → v0.1.2 漏发重演（E2E 实证：warning 语义下本批两个新测试文件差点照旧漏发）。纳管目录下「未 stage 未 ignore」几乎必是遗漏（故意不发该 gitignore） | **升级阻断**（用户） | check_release_gates.py main() rc=2 + release_template.sh 接线 `|| fail`（告警器自身失败也阻断，fail-closed）+ test_release_gates.py ×2 CLI 用例 + template-release delta scenario 措辞 |
| F3 | 低 | 阶段 2 design doc F9 节未闭环 4.2 发现：容器注入面 = 三值 + 模型路由（roc 拒裸 id、`glm-*` 直传全绿）——只在 verification-4.2-5.1.md，hub 切片设计会按「三值」踩同一坑 | 修（用户） | server-deployment design doc F9 节补「注入面实测补记」 |
| F4 | 低 | `pa-executor:latest` 默认值零注释，与 D9「版本锚定」卖点有措辞张力（名字维度与 tag 策略正交，但无声明） | 修（用户） | container_sandbox.py 两处默认值注释（latest 仅兜底、生产显式传 tag） |
| F5 | 低 | CONTEXT.md 术语表缺 pa-executor / build context 词条（D3 特意约定的规范术语未收录，镜像实体是阶段 2 核心实体） | 修（用户） | CONTEXT.md 产品化拓扑节补两词条 |
| F6 | 低 | isolated-observable-execution「only the target worktree writable」未限定**宿主挂载面**——字面读与镜像现实冲突（/opt/pa、/home/pa 容器内本就可写） | 记录不修 | 本表；合理解读 = host 暴露面语义（spec 上下文支持），归档时 D10 可补注记 |
| F7 | 低 | secrets 扫描 `--exclude='test_*.py'` 盲区大于「7 个已知文件」（任何 test 文件塞真凭据都绕过） | 记录不修 | 本表；威胁模型内部可信 + spec scenario 已注记（review 把守） |
| F8 | 低 | CREDENTIAL_PATTERNS 跨两 capability 共享，真源位置未锚进 spec（代码注释有「增删须过 change 评审」） | 记录不修 | 本表；真源 = release/check_release_gates.py，build 脚手经动态导入同源消费（不重复维护） |

## 2. 全绿项（审过无张力）

- **D9 迁移零残留**：container_sandbox.py:181/282 默认 `pa-executor:latest`，仓内无 `pa-sandbox` 字面量（唯一保留 = Dockerfile 溯源注释）——实体同一性成立（正式化替代，非并存）
- **D10 范畴分工**：executor-image delta 只规约镜像本体契约，运行时隔离语义显式让渡给 isolated-observable-execution；F9 三值 env 注入 vs 主 spec「credentials never copied into worktree」各管一层（env ≠ 文件拷贝），相容
- **module-skip 模式**：release 工具链测试在 template 副本 skip，与「release/ 不进投影」的内容契约互证（F13 自洽）
- **chown pa 即弃容器语义**：注释声明隔离边界在容器不在文件属主；即弃容器内 /opt/pa 可写不构成宿主暴露（与 F6 同一解读框架）
- **proposal 非目标无越界**：docker run 单命令指导 ≠ compose 编排（hub/compose 明确切片外）
- **自检七项 ⊃ spec 最小四项**：实现超集，无违反（spec 是最小集）
- **label unknown 兜底**：仅异常 context（缺 RELEASE_NOTE）出现；真 tag checkout 必有锚定

## 3. 复验证据

- test_release_gates.py：12 passed（含 F2 新增 CLI 阻断 ×2）
- test_build_executor_image.py：8 passed（含 F1b 新增 git 差集 ×2）
- quality.sh / release 干跑 / ruff：见 tasks 2.3、5.1 段（本批修改后全量复验记录在会话）
