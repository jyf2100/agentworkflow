# executor-image Delta

## ADDED Requirements

### Requirement: template tag 是镜像唯一内容源
执行镜像 `pa-executor` SHALL 只从 template tag 的 checkout（或等价导出）作为 build context 构建，MUST NOT 存在绕过 template 投影的镜像内容来源（本地散文件、vault 直拷）。镜像与投影同版本咬合：template tag 即镜像内容版本。

#### Scenario: 从 tag 构建成
- **WHEN** build 脚手在给定 template tag（如 v0.1.2）的 checkout 目录内执行 docker build
- **THEN** 产出 `pa-executor` 镜像，镜像 label 记录该 template tag 与其上游 vault commit（继承 RELEASE_NOTE 锚定信息）

#### Scenario: build context 混入投影外文件
- **WHEN** build 目录内存在 template tag 内容清单之外的散文件且未被 .dockerignore 排除
- **THEN** build 自检报告偏离并失败，不产出镜像
- **实现语义（本体论审核 F1 方案 b）**：清单真源（manifest）不随投影，context 侧机械判据 = git 差集——git checkout（真发布场景，clone 即 git repo）内「未跟踪未忽略」文件非空即 fail（判据严于 .dockerignore 口径，宁严勿漏）；等价导出（非 git 目录）无机械判据，build 告警跳过、由 review 把守

### Requirement: 镜像内容全集
镜像 SHALL 包含执行链运行全集：Python ≥3.11 + pa 投影（scripts/、bootstrap、webapp）、node 与 claude CLI（headless `-p` 模式可用）、gh CLI、`.claude/agents/` 8 个 persona。目标仓代码 MUST NOT 打进镜像（运行时 clone 进容器 worktree——F6：容器内 worktree 是目标面投影）。

#### Scenario: 容器内最小入口全可用
- **WHEN** 镜像自检在容器内依次执行 `python3 --version`、`claude --version`、`gh --version`、`pytest --collect-only -q`（pa scripts）
- **THEN** 四项全部退出码 0（pytest 收集非空）

### Requirement: 凭据零入镜像
Dockerfile 与 build 过程 MUST NOT 内嵌任何凭据（token、key、密码、cookie）；per-user 凭据（`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `GH_TOKEN`，F9 三值）只在运行时经 `--env-file`/secrets 注入。build 自检 SHALL 含密钥模式扫描。

#### Scenario: 密钥扫描零命中
- **WHEN** build 自检对镜像层内容与 Dockerfile 做密钥模式扫描（token/key/password 高熵与已知前缀模式；`test_*.py` 内的合成 fixture 凭据形状串除外——sanitize/凭据测试的职责载荷，非机密，真凭据风险由 review 把守）
- **THEN** 非测试载荷零命中；命中即 build 失败并列出位置

#### Scenario: 无凭据也能起容器
- **WHEN** 不注入任何凭据 env 起容器并跑机械段（如 pytest 收集、compileall）
- **THEN** 机械段正常完成——镜像本体对凭据零依赖，凭据只影响语义段（LLM 调用）

### Requirement: 隔离基线可被运行时约束
镜像 SHALL 以非 root 用户为默认运行身份，entrypoint 收敛为流水线入口；网络白名单与 CPU/内存限额由运行时（hub/compose 参数）给定，镜像 MUST NOT 内置放宽（如 `--privileged` 基线、root 默认、预开网络）。范畴分工：本 capability 只规约镜像本体契约；运行时隔离语义（tier 声明、资源限额实施、网络策略执行、telemetry）归既有 capability `isolated-observable-execution`——镜像即其 `isolated-container` tier 的执行载体。

#### Scenario: 非 root 运行
- **WHEN** 容器以默认配置启动并执行 `id -u`
- **THEN** 输出非 0（非 root）

### Requirement: pa-executor 是 pa-sandbox 的正式化替代
`pa-executor` SHALL 作为执行容器镜像的唯一规范名，即既有隐式镜像 `pa-sandbox:latest`（`container_sandbox.py` 默认值）的正式化（补内容契约 + 版本锚定），MUST NOT 与之并存为两个镜像实体。实现 SHALL 迁移 `container_sandbox.py` 默认 image 名（含测试引用）至 `pa-executor`。

#### Scenario: 默认镜像名迁移
- **WHEN** 交付后检查 `container_sandbox.py` 的 runner 默认 image 参数与全部 `pa-sandbox` 引用
- **THEN** 默认值为 `pa-executor` 系列，仓库内无残留 `pa-sandbox` 字面量（历史文档除外）

### Requirement: claude CLI 首跑状态预置
镜像 SHALL 预置 claude CLI 的非交互就绪状态（onboarding/config 初始化），使容器内首次 headless `claude -p` 调用不被首跑交互卡住（F9 登记残留项的验收）。

#### Scenario: 全新容器首跑 headless 不卡交互
- **WHEN** 在全新容器（无挂载状态）内以最小 prompt 执行 `claude -p --output-format json`（凭据经 env 注入）
- **THEN** 调用正常返回信封 JSON，不因 onboarding/trust 提示挂起或失败

### Requirement: build 脚手可复现自检
build 脚手 SHALL 一条命令从 template tag 走到带自检的镜像产物：build → 容器内最小入口自检 → 密钥扫描 → 打版本 label；自检任一失败 MUST 非零退出且不产出可用 tag。

#### Scenario: 一条命令全绿
- **WHEN** 维护者在 template tag checkout 旁执行 build 脚手
- **THEN** 产出带 label 的 `pa-executor` 镜像，自检报告全绿退出 0

#### Scenario: 自检失败不产镜像
- **WHEN** 容器内自检任一项失败（如 claude --version 非 0）
- **THEN** 脚手非零退出，不产出可引用的镜像 tag
