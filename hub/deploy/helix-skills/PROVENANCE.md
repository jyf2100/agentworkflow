# helix-skills 来源声明（PROVENANCE）

| 项 | 值 |
|---|---|
| 插件 | helix（HelixHarness，插件主体在仓库 `helixharness/` 子目录） |
| 来源 | github.com/didi/helixharness（Apache-2.0；LICENSE / NOTICE / THIRD_PARTY_NOTICES.md 仓库根与插件层各一份，已随包 vendor，见下行） |
| 快照钉定 | commit `44ffc536f73d8af525b4795ad10384c516898576`（短 hash `44ffc53`，main，2026-10-30 18:30 +0800；vault 外参考克隆 `/mnt/disk01/workspaces/worksummary/helixharness`，shallow main） |
| 快照日期 | 2026-10-31 |
| 范围 | `helixharness/skills/` 全目录 8 个 skill 原名快照：brainstorming、code-review、d2c-fidelity-evaluator、d-d2c、feedback、git-diff-impact、systematic-debugging、tdd（约 1.3M，d-d2c 含 rules/config/scripts 自包含资产） |
| 随包 notice | 本目录顶层 vendor 插件层三件：`LICENSE` / `NOTICE`（与上游仓库根两件逐字节一致，diff 已核）/ `THIRD_PARTY_NOTICES.md`（取插件层详版 5665B——含 `## superpowers`、`## mattpocock/skills` 逐文件 MIT 保留条款与改编表；上游根层同名文件是含平台组件的路由表，非详版）。Apache-2.0 再分发的保留义务随快照走；顶层文件不进容器技能目录（拷贝层按目录遍历），随投影 `/opt/pa/hub/deploy/helix-skills/` 可达 |
| 用途 | pa-executor 镜像内置 helix 技能面（spec: execution-hub「executor 镜像 helix 技能面」），COPY 至容器 `/home/pa/.claude/skills/`。**注意：容器安装 6/8**——`tdd`、`code-review` 与 executor-skills（mattpocock）同名，incumbent 让位原则跳过（设计定夺见 change add-helix-target-harness D2：helix 版深度绑定 /helix:* 工作流与插件运行时，容器内不装插件主体；executor-skills 自包含通用版保留） |
| 偏差记录 | **d-d2c/SKILL.md frontmatter `hooks:` 块已移除**（上游 PreToolUse AskUserQuestion 守卫 + Stop 守卫，引用 `${CLAUDE_PLUGIN_ROOT}/hosts/claude-code/d2c_hook_entry.cjs`）。依据：claude code 官方 skills 文档将 `hooks` 列为 SKILL.md frontmatter 字段（「registers when the skill is invoked and keeps running for the rest of the session」，个人/项目级 skill 同样生效）；快照形态无插件运行时，`${CLAUDE_PLUGIN_ROOT}` 悬空 → 守卫按上游自带 fallback **确定性 deny AskUserQuestion / block Stop**（d-d2c 被调用的会话后半程全毁）。该文件属 Apache-2.0 §4.1 修改文件：文件头（frontmatter 后首行）已置修改标注块，其余内容与上游 byte-identical（diff 已核：仅 hooks 块删除 + 标注块新增）。容器 CLI 2.1.220 的实际解析行为未在本机实证（无凭据），属防御性中和；镜像冒烟含验证项（change tasks 2.5） |
| 不含 | 插件主体（commands / hooks 运行时 / scripts / hosts / reference/ 协议文档）与 agents 16 个（3 init-*-bootstrap + 12 scan-* + unit-test-worker，ls 与 find 双口径核 @44ffc53）一律不进镜像——纯技能快照，无遥测 consent 面 |
| 随包进镜像 | 是（本文件与 notice 三件随目录一起 COPY 进投影，容器内可溯源） |

## 刷新快照（升级流程）

```bash
# vault 外参考克隆先更新到目标 commit：
git -C /mnt/disk01/workspaces/worksummary/helixharness fetch --depth 1 origin main \
  && git -C /mnt/disk01/workspaces/worksummary/helixharness checkout <目标 commit>
SRC=/mnt/disk01/workspaces/worksummary/helixharness/helixharness
DST=hub/deploy/helix-skills
rm -rf "$DST" && mkdir -p "$DST"
for d in "$SRC/skills/"*/; do cp -r "$d" "$DST/$(basename "$d")"; done
cp "$SRC/LICENSE" "$SRC/NOTICE" "$SRC/THIRD_PARTY_NOTICES.md" "$DST/"   # notice 三件随包 vendor（§4.1 保留义务）
# 然后更新本文件 commit hash 与日期 → 全量版 Dockerfile 重建新 tag（RUNBOOK §15 演进注记路线）
# → hub.env 与 example 同步升。三件刷新后必办：
# ① 若 d-d2c 上游仍带 frontmatter hooks 块 → 重复中和（删 hooks: 块）并保留/重加文件头修改标注，
#   核对本表偏差记录仍准确（diff 上游核对偏差集仅此两项）；
# ② 同名让位 = 两个 Dockerfile 的 case 排除表（tdd / code-review）。表外新撞名不必手工盯：
#   拷贝层守卫比对 executor-skills 快照源目录，撞名即 build fail loud——处置 = 决定让位
#   （扩 case 表）或上游更名后重跑，绝不允许静默覆盖 incumbent；
# ③ 核对上游 notice 层是否有新增第三方条款（THIRD_PARTY_NOTICES 逐节对账）。
```
