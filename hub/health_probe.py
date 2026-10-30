"""hub runner 健康探针——三项一通道（add-hub-runner-pool D4，spec: execution-hub「健康探针三项与红绿灯」）。

hub 无远程 shell：容器回读是唯一通道——三项全经 docker CLI 子进程（DOCKER_HOST
per-call 注入）打到目标 daemon：
1. daemon 可达：`docker version`（Server 段）——连不上 ≠ 镜像不在（三态精神，
   与 runner._DAEMON_UNREACHABLE 同判不混）
2. 镜像在场：`docker image inspect <入队默认 tag>`——与派发时 start() 首步复查
   同通道同判（镜像缺失 = 红而非「不可达」）
3. 共享存储双挂：hub 在 data_root 写 nonce 探针文件 → 经该 daemon 起一次性
   `--rm` 容器用 pa-executor 自身镜像 `cat /probe` 回读比对（用自身镜像免额外
   预载要求，cat 是基础命令任何镜像都有；此项同时验证 daemon 可达 + 挂载视角
   换算正确 + 存储链路闭合三件事——两根未指同一数据面在此显形）

红 = 不进调度候选（db.pick_runner_for_dispatch 只取 probe_ok=1——自动调度选
红机 = 白烧失败）+ 面板红绿灯可见；机器修复是人肉 ops（Non-Goal：自动修复/
自动禁用），下轮探针或面板手动触发转绿即回候选。结果缓存 runners 表行
（probe_ok / probe_json / probed_at，D4）。
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from runner import cli_env

PROBE_TIMEOUT = 60  # 单项 CLI 上限（秒）——探针占 tick 路径，宁可红不可拖死循环


def probe_runner(row: dict, cfg, docker_bin: str = "docker") -> dict:
    """对单行 runner 跑三项探针，返回扁平 detail（{daemon/image/mount: "ok"|错误摘要}）。

    三项各自独立捕获——单项失败不遮蔽其余项结论（排查起点不丢）。只探测不写表
    （写表归 probe_and_store，测试可单测本函数零 db 依赖）。
    """
    host = row["docker_host"]
    detail: dict[str, str] = {}

    def _run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT, env=cli_env(host),
        )

    # 1) daemon 可达
    try:
        r = _run([docker_bin, "version", "--format", "{{.Server.Version}}"])
        detail["daemon"] = "ok" if r.returncode == 0 else _err(r)
    except (subprocess.TimeoutExpired, OSError) as e:
        detail["daemon"] = f"不可达: {type(e).__name__}"
    # 2) 镜像在场（入队默认 tag——派发实际用的就是它）
    try:
        r = _run([docker_bin, "image", "inspect", cfg.image_tag_default])
        detail["image"] = "ok" if r.returncode == 0 else f"镜像缺失: {cfg.image_tag_default}"
    except (subprocess.TimeoutExpired, OSError) as e:
        detail["image"] = f"查询失败: {type(e).__name__}"
    # 3) 共享存储双挂（nonce 回读）
    nonce = uuid.uuid4().hex
    probe_file = cfg.data_root / f".probe-{nonce}"
    try:
        probe_file.write_text(nonce, encoding="utf-8")
        mount = Path(row["runner_mount_root"])
        r = _run([docker_bin, "run", "--rm", "-v", f"{mount / probe_file.name}:/probe:ro",
                  cfg.image_tag_default, "cat", "/probe"])
        if r.returncode == 0 and r.stdout.strip() == nonce:
            detail["mount"] = "ok"
        elif r.returncode == 0:
            detail["mount"] = "回读不匹配（nonce 比对失败——两根未指同一共享数据面）"
        else:
            detail["mount"] = _err(r)
    except (subprocess.TimeoutExpired, OSError) as e:
        detail["mount"] = f"探针容器失败: {type(e).__name__}"
    finally:
        probe_file.unlink(missing_ok=True)  # 探针文件即弃（nonce 一次性）
    return detail


def _err(r: subprocess.CompletedProcess) -> str:
    return (r.stderr or f"rc={r.returncode}").strip()[:200]


def probe_and_store(db, rid: int, cfg, docker_bin: str = "docker") -> dict:
    """单行探针 + 结果写表行缓存（面板手动触发与定期挂点共用入口）。

    runner 行不存在抛 KeyError（面板手动触发要看见错误）；探针单项失败不抛——
    红也是有效结论（写缓存），异常上抛仅限基础设施级。
    """
    row = db.get_runner(rid)
    if row is None:
        raise KeyError(f"runner 不存在：{rid}")
    detail = probe_runner(row, cfg, docker_bin=docker_bin)
    db.set_runner_probe(rid, all(v == "ok" for v in detail.values()), detail)
    return detail


def probe_enabled_runners(db, cfg, docker_bin: str = "docker") -> None:
    """定期挂点（daemon tick）：逐 enabled 行探针写缓存。fail-open——单行异常
    （含 db 异常）stderr 告警续跑（观察面绝不崩排度循环）；禁用行不烧探针
    （面板可见性靠上次缓存，软下线机器无新结论属正确形态）。"""
    try:
        rows = db.list_runners()
    except Exception as e:  # noqa: BLE001——观察面 fail-open
        print(f"[probe][warn] 池列表读取失败（fail-open）: {type(e).__name__}", file=sys.stderr)
        return
    for row in rows:
        if not row["enabled"]:
            continue
        try:
            probe_and_store(db, row["id"], cfg, docker_bin=docker_bin)
        except Exception as e:  # noqa: BLE001——单行失败不拦其余行
            print(f"[probe][warn] runner {row['name']} 探针异常（fail-open）: {type(e).__name__}: {e}",
                  file=sys.stderr)
