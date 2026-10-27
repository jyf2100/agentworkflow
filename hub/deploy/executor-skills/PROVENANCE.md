# executor-skills 来源声明（PROVENANCE）

| 项 | 值 |
|---|---|
| 插件 | mattpocock-skills（claude-plugins-official） |
| 版本 | 1.3.1 |
| 来源 | github.com/mattpocock/skills（MIT License） |
| 快照日期 | 2026-10-27 |
| 范围 | `skills/engineering/` 全目录 20 个 skill，拍平分类层（`engineering/<name>/` → `<name>/`），排除 `engineering/README.md` |
| 用途 | pa-executor 镜像内置技能包（spec: execution-hub「executor 镜像工程技能面」），COPY 至容器 `/home/pa/.claude/skills/` |
| 随包进镜像 | 是（本文件随目录一起 COPY，容器内可溯源） |

## 刷新快照（升级流程）

```bash
SRC=~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/<新版本>/skills/engineering
DST=hub/deploy/executor-skills
rm -rf "$DST" && mkdir -p "$DST"
for d in "$SRC"/*/; do cp -r "$d" "$DST/$(basename "$d")"; done
# 然后更新本文件版本与日期 → patch.Dockerfile FROM 上一 tag → build 新 tag → hub.env 与 example 同步升
```
