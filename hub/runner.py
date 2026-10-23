"""hub ContainerRunner 接缝——容器运行时抽象（add-hub-skeleton 任务 5.2/6.1-6.3，design D5）。

F13 镜像咬合：镜像存在性检查是本接缝的职责（Fake 对 fake 清单、Docker 对
`docker image inspect` 同判）；缺失 fail 暴露并带 build 提示，绝不回退其他
tag。隔离语义（非 root/资源限额/网络策略）的契约归
isolated-observable-execution capability——hub 仅传参与施加（A5 范畴声明）：
非 root 是镜像默认，hub 不加 --user；限额经 --cpus/--memory 施加。

挂载契约（hub ↔ executor 镜像，对齐镜像事实：vault 投影 = `/opt/pa`，ENTRYPOINT 已收敛
`python3 /opt/pa/Projects/项目推进流水线/scripts/run_daily.py`；`/workspace` 是目标面
worktree 挂载落点（design D6），hub 绝不挂不碰——两个范畴不混）：
- state 卷（写面）：host `<data_root>/<project>/state` → 容器 `/opt/pa/.project-auto/state`
  （run_daily.py 以 VAULT_ROOT=parents[3] 推导 `.project-auto/state`；镜像层无此目录，运行时挂载）
- profiles 卷（只读）：host `<data_root>/<project>/profiles` → 容器
  `/opt/pa/.project-auto/profiles`（inject 段校验 project ∈ load_profiles() 必读）
- sources.yaml（只读单文件）：host `<data_root>/<project>/sources.yaml` → 容器
  `/opt/pa/.project-auto/sources.yaml`（_run_pipeline 顶部无条件 load_sources() 必读）
- PRD 只读输入卷：host PRD 文件 → 容器 `/input/<prd>.md:ro`（A2：PRD 经只读卷进容器）
- 容器命令：传给镜像 ENTRYPOINT 的**纯参数**（docker run tag 后 args 附加到 ENTRYPOINT）——
  `--from-stage inject --inject-prd /input/<prd>.md --stamp <预分配>`
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CONTAINER_VAULT = "/opt/pa"                       # 镜像内 vault 投影（Dockerfile COPY 落点）
CONTAINER_PA_HOME = f"{CONTAINER_VAULT}/.project-auto"
CONTAINER_STATE = f"{CONTAINER_PA_HOME}/state"    # 写面（run_daily VAULT_ROOT=parents[3] 推导）
CONTAINER_PROFILES = f"{CONTAINER_PA_HOME}/profiles"   # inject 校验必读（load_profiles）
CONTAINER_SOURCES = f"{CONTAINER_PA_HOME}/sources.yaml"  # _run_pipeline 顶部无条件必读
CONTAINER_INPUT = "/input"


class ImageMissingError(Exception):
    """起容器时本地无队列项钉定的 tag 镜像——fail 暴露（F13），不回退。"""


@dataclass(frozen=True)
class RunSpec:
    """单次容器载荷：tag 钉死 + 命令 + 挂载 + 注入通道 + 资源限额参数。"""

    image_tag: str
    command: list[str]                 # 传给镜像 ENTRYPOINT（run_daily.py）的纯参数（tag 后 args）
    state_root: Path                   # host 侧 state 卷根（挂给容器内 pa 期望路径）
    project: str                       # 派发对象 project（runs/<proj>/ 布局）
    prd_input: Path | None = None      # PRD host 文件（只读输入卷挂 /input/）
    profiles_root: Path | None = None  # host 侧 profiles 目录（只读卷挂 .project-auto/profiles）
    sources_file: Path | None = None   # host 侧 sources.yaml（只读单文件挂 .project-auto/）
    env_file: Path | None = None       # 凭据 env-file（0600，容器退出后删）
    container_env: tuple[str, ...] = ()  # 非凭据 env 直传（K=V；docker -e，与凭据 env-file 分离）
    cpus: str = "2"
    memory: str = "4g"


@dataclass(frozen=True)
class ContainerStatus:
    state: str          # "running" | "exited" | "gone"
    exit_code: int | None


class ContainerRunner(Protocol):
    """运行时接缝——lifecycle 经此起容器/查状态/停止；镜像检查同判（Fake/Docker）。"""

    def image_exists(self, tag: str) -> bool: ...

    def start(self, spec: RunSpec) -> str:
        """起容器返回 container_ref；镜像缺失抛 ImageMissingError（绝不回退其他 tag）。"""
        ...

    def status(self, container_ref: str) -> ContainerStatus: ...

    def stop(self, container_ref: str) -> None: ...


class FakeRunner:
    """测试替身——可编程 fake 镜像清单、崩溃注入与容器终态（零 docker 依赖，E2E 干跑主力）。"""

    def __init__(self, images: set[str] | None = None, crash_on_start: Exception | None = None):
        self.images = set(images or ())
        self.crash_on_start = crash_on_start
        self.containers: dict[str, dict] = {}
        self._n = 0

    def image_exists(self, tag: str) -> bool:
        return tag in self.images

    def start(self, spec: RunSpec) -> str:
        if not self.image_exists(spec.image_tag):
            raise ImageMissingError(
                f"镜像缺失：{spec.image_tag}（先手动 build 该 tag 再重新入队；不回退其他 tag）"
            )
        if self.crash_on_start is not None:
            raise self.crash_on_start
        self._n += 1
        ref = f"fake-{self._n}"
        self.containers[ref] = {"spec": spec, "state": "running", "exit_code": None}
        return ref

    def status(self, container_ref: str) -> ContainerStatus:
        c = self.containers.get(container_ref)
        if c is None:
            return ContainerStatus(state="gone", exit_code=None)
        return ContainerStatus(state=c["state"], exit_code=c["exit_code"])

    def finish(self, container_ref: str, exit_code: int) -> None:
        """测试驱动：让 fake 容器到达终态。"""
        self.containers[container_ref]["state"] = "exited"
        self.containers[container_ref]["exit_code"] = exit_code

    def stop(self, container_ref: str) -> None:
        c = self.containers.get(container_ref)
        if c is not None and c["state"] == "running":
            c["state"] = "gone"  # 已终态容器 stop 幂等，不复活不改码


class DockerRunner:
    """docker CLI 包装——无 SDK 依赖（cron 非 login shell 的教训：依赖极薄）。"""

    def __init__(self, docker_bin: str = "docker"):
        self._bin = docker_bin

    # daemon 不可达特征（三态精神：UNKNOWN ≠ NOT_FOUND——连不上≠镜像不在，
    # 5.3 真机坐实 permission denied 曾被误报「镜像缺失」进 fail 终态带偏排障）
    _DAEMON_UNREACHABLE = ("cannot connect", "permission denied", "is the docker daemon running")

    def image_exists(self, tag: str) -> bool:
        proc = subprocess.run(
            [self._bin, "image", "inspect", tag], capture_output=True, text=True, timeout=60
        )
        if proc.returncode == 0:
            return True
        err = (proc.stderr or "").lower()
        if any(k in err for k in self._DAEMON_UNREACHABLE):
            raise RuntimeError(f"docker daemon 不可达（≠镜像缺失，不进 fail 终态）：{proc.stderr.strip()}")
        return False

    def _docker_cmd(self, spec: RunSpec) -> list[str]:
        argv = [self._bin, "run", "-d", "--cpus", spec.cpus, "--memory", spec.memory]
        if spec.env_file is not None:
            argv += ["--env-file", str(spec.env_file)]
        for kv in spec.container_env:  # 非凭据 env 直传（4b：PA_REPORT_DIR 等，与 env-file 分离）
            argv += ["-e", kv]
        argv += ["-v", f"{spec.state_root}:{CONTAINER_STATE}"]
        if spec.profiles_root is not None:
            argv += ["-v", f"{spec.profiles_root}:{CONTAINER_PROFILES}:ro"]
        if spec.sources_file is not None:
            argv += ["-v", f"{spec.sources_file}:{CONTAINER_SOURCES}:ro"]
        if spec.prd_input is not None:
            argv += ["-v", f"{spec.prd_input}:{CONTAINER_INPUT}/{spec.prd_input.name}:ro"]
        argv.append(spec.image_tag)
        return argv + list(spec.command)

    def start(self, spec: RunSpec) -> str:
        if not self.image_exists(spec.image_tag):
            raise ImageMissingError(
                f"镜像缺失：{spec.image_tag}（先手动 build 该 tag 再重新入队；不回退其他 tag）"
            )
        proc = subprocess.run(self._docker_cmd(spec), capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"docker run 失败（rc={proc.returncode}）：{proc.stderr.strip()}")
        return proc.stdout.strip()

    def status(self, container_ref: str) -> ContainerStatus:
        proc = subprocess.run(
            [self._bin, "inspect", "-f", "{{json .State}}", container_ref],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return ContainerStatus(state="gone", exit_code=None)
        state = json.loads(proc.stdout)
        if state.get("Running"):
            return ContainerStatus(state="running", exit_code=None)
        return ContainerStatus(state="exited", exit_code=state.get("ExitCode"))

    def stop(self, container_ref: str) -> None:
        if self.status(container_ref).state != "running":
            return  # gone/exited 均幂等 no-op
        subprocess.run([self._bin, "stop", container_ref], capture_output=True, timeout=120)


@contextmanager
def env_file(env: Mapping[str, str]) -> Iterator[Path]:
    """凭据 env-file 管家：0600 临时文件，作用域结束即删（容器即弃，D6）。

    与切片 2 保险箱同通道（--env-file）——切片 2 只换 env 值来源，不换此机制。
    """
    fd, name = tempfile.mkstemp(prefix="hub-env-", suffix=".env")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for k, v in env.items():
                f.write(f"{k}={v}\n")
        os.chmod(path, 0o600)
        yield path
    finally:
        path.unlink(missing_ok=True)
