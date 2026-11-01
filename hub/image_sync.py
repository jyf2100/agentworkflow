"""runner 池镜像自动分发——hub 本机 daemon → 缺片 runner 的 save|load 流式管道（add-runner-image-sync，spec: execution-hub「runner 镜像自动分发」）。

触发（D1）：探针报「daemon ok 且 image 非 ok」（探针是缺失的权威发现点；断连机烧传输
无意义）。传输（D2）：`docker save <tag> | docker -H load`——两端 CLI 都在 hub 容器内
（thin 镜像内置 docker-cli），load 侧经 cli_env(docker_host) per-call 注入（探针/派发同
通道），物理动作与 2026-10-31 人工修复同构。镜像源恒 = hub 本机 daemon，绝不公网拉取。

纪律：单一后台 worker 线程绝不阻塞排度 tick（D3）；开工复测在场即 no-op（与人工修复
赛跑安全）；失败 = MM 告警（同组合去抖、成功重置）+ runner 保持红 + item 保持 pending，
绝不静默跳过（D4）；load 后源/目标 image ID（config digest，内容寻址）比对，不一致删
目标侧 tag 按失败处理（D5——防污染派发面）；分发成功绝不直写 probe_ok——复测探针
（probe_and_store 同径）是唯一绿灯权威（D6）。
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone

import health_probe
from runner import cli_env

_INSPECT_TIMEOUT = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _image_id(docker_bin: str, tag: str, docker_host: str | None) -> str | None:
    """单侧 image ID（config digest，内容寻址）——inspect 失败返回 None（调用方判失败）。"""
    r = subprocess.run(
        [docker_bin, "image", "inspect", "-f", "{{.Id}}", tag],
        capture_output=True, text=True, timeout=_INSPECT_TIMEOUT, env=cli_env(docker_host),
    )
    return r.stdout.strip() if r.returncode == 0 else None


class ImageSyncWorker:
    """单线程后台分发 worker：任务键 (runner_id, tag) 排队/在飞双重去重（D3）。

    进程内状态无持久锁（hub 单实例既有前提）：重启丢在飞任务无害——目标侧仍缺
    → 下轮探针仍红 → 自然重触发。完成动作 = 复测探针 + 分发结果写 runners 行
    同步列 + 失败 MM 告警（去抖）。绝不阻塞调用方（offer 即返回）。
    """

    def __init__(self, runner, cfg, database, notifier=None, docker_bin: str = "docker"):
        self._runner = runner          # ContainerRunner（image_exists 三态复用）
        self._cfg = cfg
        self._db = database
        self._notifier = notifier      # None = 告警降级 stderr
        self._bin = docker_bin
        self._q: queue.Queue = queue.Queue()
        self._queued: set = set()      # (runner_id, tag) 排队去重
        self._inflight: set = set()    # (runner_id, tag) 在飞去重
        self._alert_seen: set = set()  # (runner_id, tag, reason) 失败组合去抖；成功即重置
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ── 对外面：触发判据 + 入队 ──────────────────────────────────────

    def offer(self, runner_id: int, tag: str, docker_host: str, name: str = "") -> bool:
        """入队一个分发任务（排队/在飞去重）；True = 新入队，False = 已在队列/在飞。"""
        key = (runner_id, tag)
        with self._lock:
            if key in self._queued or key in self._inflight:
                return False
            self._queued.add(key)
        self._q.put((runner_id, tag, docker_host, name))
        self._ensure_thread()
        return True

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="image-sync", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            runner_id, tag, docker_host, name = self._q.get()
            key = (runner_id, tag)
            with self._lock:
                self._queued.discard(key)
                self._inflight.add(key)
            try:
                ok, detail = self.sync_image(tag, docker_host)
            except Exception as e:  # noqa: BLE001——传输任何异常都是有效失败结论，不抛给线程
                ok, detail = False, f"传输异常：{type(e).__name__}: {e}"
            try:
                self._finish(runner_id, tag, name, ok, detail)
            except Exception as e:  # noqa: BLE001——完成动作 fail-open（观察面纪律）
                print(f"[image-sync][warn] 完成动作异常（fail-open）: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                with self._lock:
                    self._inflight.discard(key)
                self._q.task_done()

    def _finish(self, runner_id: int, tag: str, name: str, ok: bool, detail: str) -> None:
        """完成动作：分发结果写行同步列 + 复测探针（唯一绿灯权威）+ 失败告警去抖。"""
        try:
            self._db.set_runner_image_sync(runner_id, ok, tag, detail)
        except Exception as e:  # noqa: BLE001
            print(f"[image-sync][warn] 同步结果写表失败（fail-open）: {type(e).__name__}", file=sys.stderr)
        try:  # 复测探针转绿只可能经此路径（绝不直写 probe_ok——D6）
            probe_detail = health_probe.probe_and_store(self._db, runner_id, self._cfg, docker_bin=self._bin)
            probe_note = f"；复测探针 {'绿' if all(v == 'ok' for v in probe_detail.values()) else '仍有红项'}"
        except Exception as e:  # noqa: BLE001
            probe_note = f"；复测探针异常：{type(e).__name__}"
        text = (f"[pa-hub] runner {name or runner_id} 镜像分发{'成功' if ok else '失败'}：{tag}——"
                f"{detail}{probe_note}")
        print(f"[image-sync] {text}", file=sys.stderr)
        if ok:
            with self._lock:  # 成功重置该 (runner, tag) 全部失败组合（再失败可再告）
                self._alert_seen = {k for k in self._alert_seen if k[:2] != (runner_id, tag)}
            return
        akey = (runner_id, tag, detail)
        with self._lock:
            if akey in self._alert_seen:
                return  # 同失败组合去抖（进程生命周期；重启重告可接受）
            self._alert_seen.add(akey)
        if self._notifier is not None:
            self._notifier.notify_admin(text)  # fail-open 内部兜底

    # ── 传输核心：结果 (ok, detail) 返回不抛——失败也是有效结论 ────────

    def sync_image(self, tag: str, docker_host: str) -> tuple[bool, str]:
        # 1) 开工复测（权威判据 image_exists 三态，不耦合探针报文字符串）
        try:
            if self._runner.image_exists(tag, docker_host):
                return True, "目标侧已在场（no-op）"  # 人工修复与自动触发赛跑安全
        except RuntimeError as e:
            return False, f"目标 daemon 不可达，不烧传输：{e}"
        # 2) 源侧在场（hub 本机 daemon；绝不公网拉取顶替——D5 信任链）
        try:
            if not self._runner.image_exists(tag, None):
                return False, f"hub 本机无源镜像 {tag}——先在 hub 本机 build（绝不公网拉取顶替）"
        except RuntimeError as e:
            return False, f"hub 本机 daemon 不可达：{e}"
        # 3) 流式传输 docker save | docker -H load（超时终止两端）
        timeout = getattr(self._cfg, "image_sync_timeout", 900)
        save = subprocess.Popen(
            [self._bin, "save", tag], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        load = subprocess.Popen(
            [self._bin, "load"], stdin=save.stdout, stderr=subprocess.PIPE,
            env=cli_env(docker_host))
        assert save.stdout is not None
        save.stdout.close()  # 允许 save 在 load 退出后收 SIGPIPE 早退（proc 文档形态）
        try:
            _, load_err = load.communicate(timeout=timeout)
            save_err = save.stderr.read() if save.stderr else b""
            save.wait(timeout=_INSPECT_TIMEOUT)
        except subprocess.TimeoutExpired:
            load.kill()
            save.kill()
            load.communicate()
            save.communicate()
            return False, f"传输超时（>{timeout}s，已终止两端管道）"
        if save.returncode != 0:
            return False, f"docker save 失败（源侧）: {save_err.decode('utf-8', 'replace').strip()[:200]}"
        if load.returncode != 0:
            return False, f"docker load 失败（目标侧）: {(load_err or b'').decode('utf-8', 'replace').strip()[:200]}"
        # 4) 内容校验（D5）：源/目标 image ID 比对，不一致删目标侧 tag（防污染派发面）
        src_id, dst_id = _image_id(self._bin, tag, None), _image_id(self._bin, tag, docker_host)
        if not src_id or not dst_id:
            return False, f"load 后镜像 ID 取不到（源 {'有' if src_id else '无'} / 目标 {'有' if dst_id else '无'}）"
        if src_id != dst_id:
            subprocess.run([self._bin, "rmi", tag], capture_output=True, timeout=_INSPECT_TIMEOUT,
                           env=cli_env(docker_host))
            return False, f"镜像 ID 不一致（源 {src_id[:19]}… vs 目标 {dst_id[:19]}…）——目标侧 tag 已删，按失败处理"
        return True, f"流式传输完成且 ID 一致（{src_id[:19]}…）"
