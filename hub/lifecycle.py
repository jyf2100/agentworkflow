"""hub 容器生命周期编排——派发 + 对账（add-hub-skeleton 任务 7.1-7.3，spec: execution-hub「容器生命周期」）。

把 engine（队列状态机）、runner（容器接缝）、meta_sign（F10 署名）、db（runs 索引）
串成一条派发线。两个生命周期分离（A3）：run 的 failed 是容器事实，队列项的
failed 是放弃决定。hub 崩溃不杀容器（docker -d 进程树分离）——重启对账以
Runner.status + 卷上证据（journal 有无）归档终态，绝不把未知当成功（fail-safe）。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import db
import queue_engine
import runner as runner_mod
import vault_store
from meta_sign import MetaSignError, allocate_fresh_stamp, sign_meta
from pypinyin import lazy_pinyin
from runner import ImageMissingError, RunSpec, env_file

# project 目录名判据——api 入队校验与 lifecycle 派发防御共用同一常量（防路径越界同源）
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _journal_exists(run_dir: str) -> bool:
    """执行证据判据（B1）：pa 的 journal 是 `runs/<proj>/<stamp>_<slug>.journal.jsonl`
    **平铺文件**（run_daily.py 事实），与 hub 的 run 目录 `<stamp>_<slug>/` 同名同层——
    查平铺文件而非 run 目录内 glob（目录内永远空，查了判据就失真）。"""
    d = Path(run_dir)
    return (d.parent / f"{d.name}.journal.jsonl").is_file()


def _slugify(name: str) -> str:
    """PRD 文件名 → 合法 slug（[a-z0-9-]+，中文经 pypinyin 机械转写；空则 'run'）。"""
    s = "".join(lazy_pinyin(name)).lower()
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "run"


class Lifecycle:
    def __init__(
        self,
        database: db.HubDB,
        engine: queue_engine.QueueEngine,
        runner: runner_mod.ContainerRunner,
        cfg: config.HubConfig,
        clock: datetime | None = None,
        dispatch_backoff: int = 3,
        vault: vault_store.CredentialVault | None = None,
    ):
        self._db = database
        self.engine = engine
        self._runner = runner
        self.cfg = cfg  # 公开（本体论审核 B4）：api 层缺省 enqueued_by 取 cfg.user_id，无第三身份字面量
        self._clock = clock
        self._dispatch_backoff = dispatch_backoff  # 0 = 禁用退避；daemon 故障后隔 N tick 再试（B2）
        self._dispatch_cooldown = 0
        self._vault = vault  # None = 未配 master key（切片 1 env-only 合法形态，非降级）

    def _credentials(self) -> tuple[dict[str, str] | None, str]:
        """凭据来源（2a D3）：vault 优先 → env 兜底（warn 可见）→ 双缺明确报错。

        vault 配了但无该 user 记录 = 降级 env，warn stderr 可追溯；vault 未配置 =
        切片 1 env-only 合法形态，不 warn。返回 (env 字典, 来源)；双缺/保险箱坏 →
        (None, 缺失说明)——调用方 fail fast，绝不空值注入。
        """
        if self._vault is not None:
            try:
                rec = self._vault.get_user_env(self.cfg.user_id)
            except vault_store.VaultError as e:
                return None, f"保险箱读取失败：{e}"
            if rec is not None:
                missing = [k for k in vault_store.VAULT_REQUIRED_KEYS if not rec.get(k)]
                return (rec, "vault") if not missing else (None, f"保险箱记录缺必需字段：{', '.join(missing)}")
            print(
                f"[warn] 保险箱无 user={self.cfg.user_id} 记录——降级 hub 进程 env 直注"
                f"（spec: execution-hub「单用户凭据来源」）",
                file=sys.stderr,
            )
        env = self.cfg.user_env()
        missing = [k for k in vault_store.VAULT_REQUIRED_KEYS if not env.get(k)]
        if missing:
            return None, f"vault 与 env 均无必需凭据：缺 {', '.join(missing)}"
        return env, "env"

    # ── 派发（7.1）───────────────────────────────────────────────

    def tick_dispatch(self) -> bool:
        """单次派发：pick → 预分配 → 署名 → 起容器。False = 无可派或派发失败。"""
        if self._dispatch_cooldown > 0:  # 退避中（B2）：跳过本 tick，不重复记账
            self._dispatch_cooldown -= 1
            return False
        item = self.engine.pick_next()
        if item is None:
            return False

        project = item["project"]
        if not PROJECT_RE.fullmatch(project):
            self.engine.fail_item(item["id"], f"project 名非法：{project!r}（拒绝派发，防路径越界）")
            return False
        prd = Path(item["prd_path"])
        if not prd.is_file():
            self.engine.fail_item(item["id"], f"PRD 文件不存在：{prd}（重新入队正确路径）")
            return False

        state_root = self.cfg.data_root / project / "state"
        profiles_root = self.cfg.data_root / project / "profiles"
        sources_file = self.cfg.data_root / project / "sources.yaml"
        if not profiles_root.is_dir() or not sources_file.is_file():
            # fail fast（镜像咬合）：pa 启动无条件读 sources.yaml + inject 校验 profiles——
            # 缺则容器必崩非零退，白白消耗重派名额；不猜，补齐后重新入队
            self.engine.fail_item(
                item["id"],
                f"启动必读面缺失（镜像内 pa 无条件读取）：profiles={profiles_root}"
                f"（存在={profiles_root.is_dir()}）、sources.yaml={sources_file}"
                f"（存在={sources_file.is_file()}）——补齐后重新入队",
            )
            return False
        user_env, cred_msg = self._credentials()
        if user_env is None:  # 双缺/保险箱坏——fail fast 不空值注入（spec scenario「缺凭据明确报错」）
            self.engine.fail_item(item["id"], f"凭据缺失（不静默空值注入）：{cred_msg}")
            return False
        state_root.mkdir(parents=True, exist_ok=True)
        now = self._clock or datetime.now(timezone.utc)
        stamp = allocate_fresh_stamp(state_root, now.strftime("%Y%m%d"))
        slug = _slugify(prd.stem)
        run_dir = state_root / "runs" / project / f"{stamp}_{slug}"

        try:
            sign_meta(
                run_dir, stamp, slug,
                initiator=self.cfg.user_id, queue_item_id=item["id"],
                image_tag=item["image_tag"], now=now,
            )
        except MetaSignError as e:
            self.engine.fail_item(item["id"], f"meta 署名失败：{e}")
            return False

        rid = self._db.create_run(item["id"], project, str(run_dir))
        # 纯参数（镜像 ENTRYPOINT 已收敛 run_daily.py——docker run tag 后 args 附加到 ENTRYPOINT）
        cmd = ["--from-stage", "inject", "--inject-prd", f"/input/{prd.name}", "--stamp", stamp]
        with env_file(user_env) as ef:
            spec = RunSpec(
                image_tag=item["image_tag"], command=cmd, state_root=state_root,
                project=project, prd_input=prd, profiles_root=profiles_root,
                sources_file=sources_file, env_file=ef,
            )
            try:
                ref = self._runner.start(spec)
            except ImageMissingError as e:
                self._db.set_run_finished(rid, "failed_to_start")
                self.engine.fail_item(item["id"], str(e))  # F13：终态，修环境后重新入队
                return False
            except Exception as e:  # daemon 故障等基础设施异常——run 记账，item 保持 pending 重试
                self._db.set_run_finished(rid, "failed_to_start")
                self.engine.note_error(item["id"], f"起容器异常：{e}")
                self._dispatch_cooldown = self._dispatch_backoff  # 连续失败退避（B2）：冷却期不再逐 tick 记账
                return False

        self._dispatch_cooldown = 0  # 派发成功：退避清零
        self._db.set_run_running(rid, ref)
        self.engine.mark_running(item["id"])
        return True

    # ── 对账（7.3 + 7.2）─────────────────────────────────────────

    def reconcile_running(self) -> None:
        """轮询 running run 行：exited → 记终态走状态机；gone → 按卷证据归档。"""
        for run in self._db.runs_in_status("running"):
            st = self._runner.status(run["container_ref"])
            if st.state == "running":
                continue
            if st.state == "exited":
                rc = st.exit_code if st.exit_code is not None else 1  # None 不该出现，兜底按失败
                status = "succeeded" if rc == 0 else "failed"
                self._db.set_run_finished(run["id"], status, rc)
                self.engine.on_exit(run["queue_item_id"], rc)
            else:  # gone——终态不明，以卷上 journal 证据归档，绝不静默当成功
                has_journal = _journal_exists(run["run_dir"])
                status = "failed" if has_journal else "failed_to_start"
                self._db.set_run_finished(run["id"], status, None)
                self.engine.on_exit(run["queue_item_id"], 1)

    def reconcile_startup(self) -> None:
        """hub 重启对账：先对账在跑容器（7.3），再归档卷上孤儿 meta（7.2，F10）。"""
        self.reconcile_running()
        known = {r["run_dir"] for r in self._db.list_all_runs()}
        root = self.cfg.data_root
        if not root.is_dir():
            return
        for proj_dir in sorted(root.iterdir()):
            if not proj_dir.is_dir() or not PROJECT_RE.fullmatch(proj_dir.name):
                continue
            runs_dir = proj_dir / "state" / "runs" / proj_dir.name
            if not runs_dir.is_dir():
                continue
            for run_dir in sorted(runs_dir.iterdir()):
                self._archive_orphan_if_any(run_dir, proj_dir.name, known)

    def _archive_orphan_if_any(self, run_dir: Path, project: str, known: set[str]) -> None:
        """有 meta 无 journal 且 DB 无记录 → 补索引 failed_to_start（meta 保留不清理）。"""
        if not run_dir.is_dir() or str(run_dir) in known:
            return
        metas = list(run_dir.glob("*.meta.json"))
        if not metas or _journal_exists(str(run_dir)):
            return  # 无 meta 非本系统目录；有平铺 journal 则容器跑过，非孤儿
        try:
            payload = json.loads(metas[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # 坏 meta 不猜——留卷上待人工（fail 暴露但不静默编造终态）
        rid = self._db.create_run(int(payload.get("queue_item_id") or 0), project, str(run_dir))
        self._db.set_run_finished(rid, "failed_to_start")
