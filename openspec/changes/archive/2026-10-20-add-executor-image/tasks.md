## 1. Dockerfile 与投影纳管

- [x] 1.1 撰写 `release/Dockerfile`（D2/D5/D6：node:22-slim 基底、python3.11、claude CLI 版本 ARG 钉扎、gh CLI、非 root 用户 `pa`、WORKDIR /workspace、首跑状态预置）+ `release/.dockerignore`
- [x] 1.2 `release/manifest.txt` 纳管两文件（投影到 template 根；新文件先 git add 再投影——F12 教训）；跑一次投影 dry-run 确认落点（全绿 rc=0；顺手修：template-release 主 spec 例文 `/Users/roc` 字面量命中自家 grep 门禁 → 改占位写法 `/Users/<user>`——归档转正引入的 v0.1.3 必炸项）

## 2. release 静态自检扩展（D7）

- [x] 2.1 自检函数：Dockerfile 四项静态检查（存在 / FROM 显式 tag / USER 非 root / 无凭据字面量模式）——TDD：先写失败测试再实现
- [x] 2.2 工作树 vs `git ls-files` 差集告警（F12 登记项闭环：新文件未 stage → 警告列出差集）——TDD
- [x] 2.3 两项接进 release 自检链（任一失败/告警按语义阻断或提示）；`quality.sh` 全绿（2038 passed + ruff 零告警；干跑 rc=0：F12 告警命中本批未 add 新文件＝预期、四项静态检查过）

## 3. build 脚手（D3/D4）

- [x] 3.1 `release/build_executor_image.sh`：checkout/复用 template tag → docker build（ARG 钉扎版本）→ label（template tag + upstream commit + 组件实际版本清单）→ 容器内自检序列 → 全绿退出 0
- [x] 3.2 `--dry-run` 模式（打印计划序列不执行）+ pytest 断言参数与顺序（D8 接缝；6 用例：序列契约/label 与 tag/ARG 解析/upstream commit 两态/无 Dockerfile fail-fast/静态违规阻断/用法错误）

## 4. 镜像自检、首跑验收与 pa-sandbox 迁移

- [x] 4.1 容器内自检清单落地：id -u / python3 / claude / gh 版本 / pytest --collect-only 非空 / compileall / 密钥模式扫描（随 3.1 落进 build_executor_image.sh 的 run_check 七项序列；密钥模式与 CREDENTIAL_PATTERNS 同源动态生成）
- [x] 4.2 全新容器 headless 实测：最小 `claude -p --output-format json` 不因 onboarding/trust 挂起（F9 残留项验收；发现的额外状态项补进 Dockerfile 并记录）——实测不挂起✓；额外状态项=模型路由非镜像层（roc 拒裸 id，glm-5.2 直传全绿 `is_error:False`）→ 记 hub 注入面设计输入，见 verification-4.2-5.1.md
- [x] 4.3 pa-sandbox 正式化迁移（D9）：`container_sandbox.py` 默认 image 名两处（181/282）+ 全部测试引用迁至 `pa-executor`，仓库无残留 `pa-sandbox` 字面量（实测：代码/测试/specs 零引用，唯一保留 = Dockerfile 内 D9 裁决溯源注释；sandbox 相关 82 测试全绿）

## 5. E2E 与发布

- [x] 5.1 本服务器 E2E（发布前验证）：从**已纳管 Dockerfile 的投影工作副本** build 全绿（v0.1.2 tag 无 Dockerfile，不可作 build context——勿重蹈 tasks 原序错误）；记 verification 文档——`pa-executor:v0.1.3-rc1` 七项自检全绿 BUILD_RC=0；干跑抓出 6 个真问题全部回流修复（syntax 行/mirror 403/gh 落位/chown/secrets 排除/curl 重试），见 verification-4.2-5.1.md
- [x] 5.2 `release/docs/CLAUDE.template.md` 补「可选容器化」一节（docker build 用法；直跑仍是默认）——含 env 三值注入说明与 `/workspace` 目标面挂载语义
- [x] 5.3 发布 v0.1.3（Dockerfile 首次随投影发布），release note 注明 executor-image 中间态与 pa-sandbox→pa-executor 更名——已推（快照模型 force push main，v0.1.0-0.1.2 tag 锚定不丢；副本内 quality 2020 passed + 3 skipped，upstream e650069）
- [x] 5.4 从 v0.1.3 tag 复验 build（tag 即 build context 的闭环实证）——`--remote` clone 完整链 BUILD_RC=0：F1b git 差集校验首次真形态生效（clone 即 git repo，差集 0，未走等价导出告警分支）；label 五项全对（template-tag v0.1.3 / upstream e650069 自动从 tag 内 RELEASE_NOTE 抓取 / claude 2.1.220 / gh 2.102.0）；七项自检全绿
