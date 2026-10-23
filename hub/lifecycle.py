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
import events
import notify
import queue_engine
import runner as runner_mod
import vault_store
from meta_sign import MetaSignError, allocate_fresh_stamp, sign_meta
from pypinyin import lazy_pinyin
from runner import CONTAINER_STATE, ImageMissingError, RunSpec, env_file

# project 目录名判据——api 入队校验与 lifecycle 派发防御共用同一常量（防路径越界同源）
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _event_broadcast(row: dict) -> bool:
    """频道广播判据（4b spec：critic drop / verify 红进频道 + 私信，其余仅私信）——
    hub 只做机械枚举比对，零语义解读。"""
    ev = row.get("event")
    if ev == "critic_verdict":
        return row.get("verdict") == "drop"
    if ev == "verify_verdict":
        return row.get("verdict") != "pass"  # 红 = 非 pass（revise 等均频道可见）
    return False  # pr_opened / critic pass 仅私信


def _event_text(run: dict, row: dict) -> str:
    """事件通知文本 = 事实字段机械拼接（hub 零语义加工），头部带 run 标识可回查。"""
    head = f"[pa-hub] {row.get('project') or run['project']} run #{run['id']}（{Path(run['run_dir']).name}）"
    ev = row.get("event")
    if ev == "critic_verdict":
        return f"{head} critic 判定 → {row.get('verdict')}\n{row.get('summary') or ''}".rstrip()
    if ev == "pr_opened":
        repo = row.get("repo")
        return f"{head} PR 开出{f'（{repo}）' if repo else ''}：{row.get('pr_url')}"
    rnd = row.get("round")
    return (f"{head} verify 判定 → {row.get('verdict')}"
            f"{'（round ' + str(rnd) + '）' if rnd is not None else ''}\n{row.get('summary') or ''}").rstrip()


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
        notifier: notify.Notifier | None = None,
    ):
        self._db = database
        self.engine = engine
        self._runner = runner
        self.cfg = cfg  # 公开（本体论审核 B4）：api 层缺省 enqueued_by 取 cfg.user_id，无第三身份字面量
        self._clock = clock
        self._dispatch_backoff = dispatch_backoff  # 0 = 禁用退避；daemon 故障后隔 N tick 再试（B2）
        self._dispatch_cooldown = 0
        self._vault = vault  # None = 未配 master key（切片 1 env-only 合法形态，非降级）
        self.notifier = notifier  # 4a D3 公开（daemon 陈旧检测复用，cfg 公开先例）：None = 通道关闭零通知
        # 4b 语义事件去重：已转发 (run_id, seq)——进程内存，run 终态即清（_finish_run），
        # 无持久写面；hub 重启重发一轮已发事件是接受的语义（文本幂等，最多一条重复私信）
        self._event_seen: set[tuple[int, int]] = set()

    def _credentials(self, user: str | None = None) -> tuple[dict[str, str] | None, str]:
        """凭据来源（2a D3 / 2b D4）：vault 优先 → env 兜底（warn 可见）→ 双缺明确报错。

        user（2b）= 队列项 enqueued_by（会话用户 = mm_user_id）；缺省 cfg.user_id
        （2a 单用户行为零变化）。vault 配了但无该 user 记录 = 降级 env，warn stderr
        可追溯；vault 未配置 = 切片 1 env-only 合法形态，不 warn。返回
        (env 字典, 来源)；双缺/保险箱坏 → (None, 缺失说明)——调用方 fail fast，
        绝不空值注入。
        """
        user = user or self.cfg.user_id
        if self._vault is not None:
            try:
                rec = self._vault.get_user_env(user)
            except vault_store.VaultError as e:
                return None, f"保险箱读取失败：{e}"
            if rec is not None:
                missing = [k for k in vault_store.VAULT_REQUIRED_KEYS if not rec.get(k)]
                return (rec, "vault") if not missing else (None, f"保险箱记录缺必需字段：{', '.join(missing)}")
            print(
                f"[warn] 保险箱无 user={user} 记录——降级 hub 进程 env 直注"
                f"（spec: execution-hub「单用户凭据来源」）",
                file=sys.stderr,
            )
        env = self.cfg.user_env()
        missing = [k for k in vault_store.VAULT_REQUIRED_KEYS if not env.get(k)]
        if missing:
            return None, f"vault 与 env 均无必需凭据：缺 {', '.join(missing)}"
        return env, "env"

    # ── run 终态收敛点（4a D3，spec: hub-notifications「run 终态通知」）────

    def _safe_item(self, item_id: int) -> dict | None:
        """孤儿归档行（queue_item_id=0 / 行已不在）查 item 失败 → None（通知回落 cfg.user_id）。"""
        try:
            return self._db.get_item(item_id)
        except db.DBError:
            return None

    def _finish_run(
        self, rid: int, status: str, *, project: str, run_dir_name: str,
        item: dict | None, exit_code: int | None = None, last_error: str | None = None,
    ) -> None:
        """落库 + 通知（4a D3）：五处 set_run_finished 的唯一收敛点——「至多一次」由挂点
        结构保证（无标记列无轮询）；通知 try/except 全捕获 fail-open，绝不阻断落库
        与后续状态机（notifier 内部对私信/频道也已各自兜底——此处兜的是构造/组装层）。"""
        self._db.set_run_finished(rid, status, exit_code)
        self._event_seen = {k for k in self._event_seen if k[0] != rid}  # 终态即清（内存有界）
        if self.notifier is None:
            return
        try:
            self.notifier.notify_run(
                run_id=rid, project=project, run_dir_name=run_dir_name, status=status,
                exit_code=exit_code, enqueued_by=(item or {}).get("enqueued_by"),
                initiator=(item or {}).get("initiator"), last_error=last_error,
            )
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面（fail-open）
            print(f"[notify][warn] run#{rid} {status} 通知失败（fail-open 不阻断）: {type(e).__name__}", file=sys.stderr)

    # ── 派发（7.1）───────────────────────────────────────────────

    def _abandon(self, item: dict, project: str, reason: str) -> None:
        """无 run 的队列放弃收敛点（4b 任务 4.1，spec: hub-notifications「无 run 放弃告警」）：
        create_run 前五处 fail_item 的唯一包装——落放弃 + notify_admin（含 project + 原因摘要）。
        通道关闭（notifier None）stderr 降级留痕；通知异常 fail-open 绝不阻断 fail_item。"""
        self.engine.fail_item(item["id"], reason)
        if self.notifier is None:
            print(f"[notify][warn] 队列放弃（通道关闭，仅 stderr）：item#{item['id']} {project}：{reason}", file=sys.stderr)
            return
        try:
            self.notifier.notify_admin(f"[pa-hub] 队列项 #{item['id']}（{project}）放弃，无 run 产生：\n{reason}")
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面
            print(f"[notify][warn] 放弃告警推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)

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
            self._abandon(item, project, f"project 名非法：{project!r}（拒绝派发，防路径越界）")
            return False
        prd = Path(item["prd_path"])
        if not prd.is_file():
            self._abandon(item, project, f"PRD 文件不存在：{prd}（重新入队正确路径）")
            return False

        state_root = self.cfg.data_root / project / "state"
        profiles_root = self.cfg.data_root / project / "profiles"
        sources_file = self.cfg.data_root / project / "sources.yaml"
        if not profiles_root.is_dir() or not sources_file.is_file():
            # fail fast（镜像咬合）：pa 启动无条件读 sources.yaml + inject 校验 profiles——
            # 缺则容器必崩非零退，白白消耗重派名额；不猜，补齐后重新入队
            self._abandon(
                item, project,
                f"启动必读面缺失（镜像内 pa 无条件读取）：profiles={profiles_root}"
                f"（存在={profiles_root.is_dir()}）、sources.yaml={sources_file}"
                f"（存在={sources_file.is_file()}）——补齐后重新入队",
            )
            return False
        user_env, cred_msg = self._credentials(item["enqueued_by"])  # 2b D4：按队列项发起人取凭据
        if user_env is None:  # 双缺/保险箱坏——fail fast 不空值注入（spec scenario「缺凭据明确报错」）
            self._abandon(item, project, f"凭据缺失（不静默空值注入）：{cred_msg}")
            return False
        state_root.mkdir(parents=True, exist_ok=True)
        now = self._clock or datetime.now(timezone.utc)
        stamp = allocate_fresh_stamp(state_root, now.strftime("%Y%m%d"))
        slug = _slugify(prd.stem)
        run_dir = state_root / "runs" / project / f"{stamp}_{slug}"

        try:
            sign_meta(
                run_dir, stamp, slug,
                initiator=item.get("initiator") or item["enqueued_by"],  # 2b D4：会话人读名，无则回落队列项发起人
                queue_item_id=item["id"],
                image_tag=item["image_tag"], now=now,
            )
        except MetaSignError as e:
            self._abandon(item, project, f"meta 署名失败：{e}")
            return False

        rid = self._db.create_run(item["id"], project, str(run_dir))
        # 纯参数（镜像 ENTRYPOINT 已收敛 run_daily.py——docker run tag 后 args 附加到 ENTRYPOINT）
        cmd = ["--from-stage", "inject", "--inject-prd", f"/input/{prd.name}", "--stamp", stamp]
        with env_file(user_env) as ef:
            spec = RunSpec(
                image_tag=item["image_tag"], command=cmd, state_root=state_root,
                project=project, prd_input=prd, profiles_root=profiles_root,
                sources_file=sources_file, env_file=ef,
                # 4b 任务 3.3：report 双写出口（docker -e 直传——非凭据，与 env-file 分离）；
                # 落容器内 state 卷（host 可见），run 详情页按 stamp 对齐读
                container_env=(f"PA_REPORT_DIR={CONTAINER_STATE}/reports",),
            )
            try:
                ref = self._runner.start(spec)
            except ImageMissingError as e:
                self._finish_run(rid, "failed_to_start", project=project, run_dir_name=run_dir.name,
                                 item=item, last_error=str(e))
                self.engine.fail_item(item["id"], str(e))  # F13：终态，修环境后重新入队
                return False
            except Exception as e:  # daemon 故障等基础设施异常——run 记账，item 保持 pending 重试
                self._finish_run(rid, "failed_to_start", project=project, run_dir_name=run_dir.name,
                                 item=item, last_error=f"起容器异常：{e}")
                self.engine.note_error(item["id"], f"起容器异常：{e}")
                self._dispatch_cooldown = self._dispatch_backoff  # 连续失败退避（B2）：冷却期不再逐 tick 记账
                return False

        self._dispatch_cooldown = 0  # 派发成功：退避清零
        self._db.set_run_running(rid, ref)
        self.engine.mark_running(item["id"])
        return True

    # ── 对账（7.3 + 7.2）─────────────────────────────────────────

    def _forward_events(self, run: dict) -> None:
        """语义事件转发（4b 任务 3.2，spec: pa-event-outlet hub 读面）：running run 每轮
        reconcile 读伴生 events.jsonl，(run_id, seq) 进程内存去重后新行经 notifier 转发。

        寻址不依赖 hub run 目录 slug（_slugify 后与 pa stem 可能不同）——从
        queue_item.prd_path 拿 PRD stem 机械拼 `{stamp}_{stem}.events.jsonl`
        （stamp = run 目录名前缀 = 派发预分配，与 pa 侧写点逐字节同源）。孤儿 run
        （item 查无）无 prd_path 可拼 → 跳过。run 终态后不再进本方法（读取面在
        running 扫描集内）。fail-open：任一异常 stderr 告警不抛（reconcile 同步路径）。
        """
        if self.notifier is None:
            return
        try:
            item = self._safe_item(run["queue_item_id"])
            if item is None:
                return  # 孤儿归档行无 prd_path 可拼——跳过（终态通知已覆盖该 run）
            stem = Path(item["prd_path"]).stem
            run_dir = Path(run["run_dir"])
            ev_file = run_dir.parent / f"{run_dir.name.split('_', 1)[0]}_{stem}.events.jsonl"
            for row in events.read_events(ev_file):
                key = (run["id"], row.get("seq"))
                if key in self._event_seen:
                    continue
                self._event_seen.add(key)
                self.notifier.notify_event(
                    text=_event_text(run, row), enqueued_by=item.get("enqueued_by"),
                    project=run["project"], broadcast=_event_broadcast(row),
                )
        except Exception as e:  # 观察面 fail-open——绝不炸 reconcile 主路径
            print(f"[events][warn] run#{run['id']} 事件转发失败（fail-open）: {type(e).__name__}", file=sys.stderr)

    def reconcile_running(self) -> None:
        """轮询 running run 行：exited → 记终态走状态机；gone → 按卷证据归档。"""
        for run in self._db.runs_in_status("running"):
            st = self._runner.status(run["container_ref"])
            if st.state == "running":
                self._forward_events(run)  # 4b：running 中读伴生事件文件转发新行（观察面）
                continue
            if st.state == "exited":
                rc = st.exit_code if st.exit_code is not None else 1  # None 不该出现，兜底按失败
                status = "succeeded" if rc == 0 else "failed"
                self._finish_run(run["id"], status, exit_code=rc, project=run["project"],
                                 run_dir_name=Path(run["run_dir"]).name,
                                 item=self._safe_item(run["queue_item_id"]))
                self.engine.on_exit(run["queue_item_id"], rc)
            else:  # gone——终态不明，以卷上 journal 证据归档，绝不静默当成功
                has_journal = _journal_exists(run["run_dir"])
                status = "failed" if has_journal else "failed_to_start"
                self._finish_run(run["id"], status, project=run["project"],
                                 run_dir_name=Path(run["run_dir"]).name,
                                 item=self._safe_item(run["queue_item_id"]),
                                 last_error="容器记录消失，按卷上 journal 证据归档" if has_journal
                                 else "容器记录消失且无 journal——归档 failed_to_start")
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
        self._finish_run(rid, "failed_to_start", project=project, run_dir_name=run_dir.name,
                         item=self._safe_item(int(payload.get("queue_item_id") or 0)),
                         last_error="孤儿 meta（hub 崩溃窗口），重启对账归档")
