"""hub 容器生命周期编排——派发 + 对账（add-hub-skeleton 任务 7.1-7.3，spec: execution-hub「容器生命周期」）。

把 engine（队列状态机）、runner（容器接缝）、meta_sign（F10 署名）、db（runs 索引）
串成一条派发线。两个生命周期分离（A3）：run 的 failed 是容器事实，队列项的
failed 是放弃决定。hub 崩溃不杀容器（docker -d 进程树分离）——重启对账以
Runner.status + 卷上证据（journal 有无）归档终态，绝不把未知当成功（fail-safe）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import db
import events
import health_probe
import notify
import queue_engine
import runner as runner_mod
import vault_store
from meta_sign import MetaSignError, allocate_fresh_stamp, sign_meta
from pypinyin import lazy_pinyin
from runner import CONTAINER_STATE, ImageMissingError, RunSpec, env_file

# project 目录名判据——api 入队校验与 lifecycle 派发防御共用同一常量（防路径越界同源）
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# profile 的 forge: 声明行（零 YAML 库逐行扫——hub 最小依赖面不动，projects.py 同口径）
_FORGE_LINE_RE = re.compile(r'^\s*forge:\s*"?([A-Za-z0-9_-]+)"?\s*$', re.M)


def _profile_forges(profiles_root: Path) -> set[str] | None:
    """项目 profiles/*.yaml 的 forge 声明集合（add-forge-adapter 3.2）。

    None/空集 = 无任何声明（github 缺省口径）。非法值原样收集（不在此处 raise）——
    必需键计算处（vault_store.forge_required_keys）fail-fast，abandon 消息带原值。
    目录缺失/读失败 = None（派发前置检查已保证启动必读面存在，此处仅兜底）。
    """
    if not profiles_root.is_dir():
        return None
    forges: set[str] = set()
    try:
        files = sorted(profiles_root.glob("*.yaml"))
    except OSError:
        return None
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        forges.update(_FORGE_LINE_RE.findall(text))
    return forges or None


def _event_text(run: dict, row: dict, rid: int | None = None) -> str:
    """事件通知文本 = 事实字段机械拼接（hub 零语义加工），头部带 run 标识可回查。"""
    head = f"[pa-hub] {row.get('project') or run['project']} run #{run['id']}（{Path(run['run_dir']).name}）"
    ev = row.get("event")
    if ev == "critic_verdict":
        return f"{head} critic 判定 → {row.get('verdict')}\n{row.get('summary') or ''}".rstrip()
    if ev == "metrics_proposed":
        # 确认环文案（2.2 spec：逐条一行 ≤8 截断；proxy 逐条标注；requires: 前置在
        # metric 文本内随行带出；@发起人由 notify_event 层 _mention 统一处理）。
        parts = [f"{head} 需求缺验收指标，已拟指标清单（确认行 #{rid}）待拍板："]
        metrics = [m for m in (row.get("metrics") or []) if isinstance(m, dict)]
        for m in metrics[:8]:
            tag = "〔代理指标〕" if m.get("coverage") == "proxy" else ""
            parts.append(f"- {m.get('metric')}{tag}（依据：{m.get('from')}）")
        if len(metrics) > 8:
            parts.append(f"（共 {len(metrics)} 条，超出 8 条截断——完整清单见确认行 #{rid}）")
        parts.append(f"用法：/pa confirm #{rid} 拍板入队 ｜ /pa reject #{rid} 放弃 ｜ "
                     f"/pa metrics #{rid} <补充> 带上下文重拟")
        return "\n".join(parts)
    if ev == "pr_opened":
        repo = row.get("repo")
        return f"{head} PR 开出{f'（{repo}）' if repo else ''}：{row.get('pr_url')}"
    if ev == "acceptance_suite":
        st = row.get("status")
        parts = [f"{head} acceptance 套件 → {st}"]
        if st == "stored":
            dg = row.get("digest")
            if dg:
                parts.append(f"digest {dg}")
            sup = row.get("supersedes")
            if sup:
                parts.append(f"supersedes {sup}（换版链）")  # 旧 digest 可回查换版轨迹
        else:
            rs, dt = row.get("reason"), row.get("detail")
            if rs:
                parts.append(f"原因 {rs}")
            if dt:
                parts.append(str(dt))
        return "\n".join(parts)
    rnd, rsn = row.get("round"), row.get("reason")
    return (f"{head} verify 判定 → {row.get('verdict')}"
            f"{'（round ' + str(rnd) + '）' if rnd is not None else ''}"
            f"{'（reason ' + str(rsn) + '）' if rsn else ''}\n{row.get('summary') or ''}").rstrip()


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

    def _credentials(self, user: str | None = None,
                     project: str | None = None) -> tuple[dict[str, str] | None, str]:
        """凭据来源（2a D3 / 2b D4）：vault 优先 → env 兜底（warn 可见）→ 双缺明确报错。

        user（2b）= 队列项 enqueued_by（会话用户 = mm_user_id）；缺省 cfg.user_id
        （2a 单用户行为零变化）。project（add-forge-adapter 3.2）= 待派发项目——必需键
        按该项目 profiles 的 forge 声明动态计算（project_required_keys）：gitlab 项目必需
        GITLAB_TOKEN、GH_TOKEN 不再无条件必需（gitlab/gitcode-only 用户不被迫造假）；
        缺省 None = github 口径（旧三值必需，单用户行为零变化）。
        vault 配了但无该 user 记录 = 降级 env，warn stderr 可追溯；vault 未配置 = 切片 1
        env-only 合法形态，不 warn。返回 (env 字典, 来源)；双缺/保险箱坏/forge 声明非法 →
        (None, 缺失说明)——调用方 fail fast，绝不空值注入。
        """
        user = user or self.cfg.user_id
        try:
            required = vault_store.project_required_keys(
                _profile_forges(self.cfg.data_root / project / "profiles") if project else None
            )
        except ValueError as e:
            return None, f"项目 forge 声明非法（不猜）：{e}"
        if self._vault is not None:
            try:
                rec = self._vault.get_user_env(user)
            except vault_store.VaultError as e:
                return None, f"保险箱读取失败：{e}"
            if rec is not None:
                missing = [k for k in required if not rec.get(k)]
                if missing:
                    return None, f"保险箱记录缺必需字段（项目 {project} 按 forge 声明）：{', '.join(missing)}"
                bad = vault_store.placeholder_issues(rec)
                if bad:
                    return None, f"保险箱记录凭据形态非法：{'；'.join(bad)}"
                # F9 模型路由（非凭据配置键）兜底注入：vault 记录无 ANTHROPIC_MODEL 时
                # HUB_USER_MODEL→ANTHROPIC_MODEL 与凭据同 env-file 通道——不 merge 则该分支
                # 无模型路由（组5.1 hub 全链实证：容器内 claude 落 CLI 内置默认模型，roc
                # 代理 400 拒裸 Anthropic id）。记录里有模型（面板凭据页所存）= 用户显式
                # 选择，优先于 hub 级缺省（2026-10-27 #56 修复：恒覆盖致面板 model 框失效）。
                if self.cfg.user_model and not rec.get("ANTHROPIC_MODEL"):
                    rec = {**rec, "ANTHROPIC_MODEL": self.cfg.user_model}
                return rec, "vault"
            print(
                f"[warn] 保险箱无 user={user} 记录——降级 hub 进程 env 直注"
                f"（spec: execution-hub「单用户凭据来源」）",
                file=sys.stderr,
            )
        env = self.cfg.user_env()
        missing = [k for k in required if not env.get(k)]
        if missing:
            return None, f"vault 与 env 均无必需凭据（项目 {project} 按 forge 声明）：缺 {', '.join(missing)}"
        bad = vault_store.placeholder_issues(env)
        if bad:
            return None, f"env 凭据形态非法（疑似模板占位未换真值）：{'；'.join(bad)}"
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
        # 业务终态 outcome 读面（add-pipeline-outcome-observability 2.1）：在手 →
        # 通知 lead 业务摘要；缺 → 回退 exit-code 文本并 stderr 留痕（不猜业务终态——
        # 旧镜像/中断 run 无 outcome 文件是常态非错误，info 级）。
        try:
            outcome = notify.load_pipeline_outcome(
                self.cfg.data_root / project / "state", run_dir_name)
        except Exception as e:  # noqa: BLE001——读面异常同回退（观察面不阻断落库路径）
            outcome = None
            print(f"[notify][warn] run#{rid} outcome 读取异常（回退 exit-code 文本）: {type(e).__name__}", file=sys.stderr)
        if outcome is None:
            print(f"[notify][info] run#{rid} 无 pipeline_outcome——通知回退 exit-code 文本", file=sys.stderr)
        try:
            self.notifier.notify_run(
                run_id=rid, project=project, run_dir_name=run_dir_name, status=status,
                exit_code=exit_code, enqueued_by=(item or {}).get("enqueued_by"),
                initiator=(item or {}).get("initiator"), last_error=last_error,
                outcome=outcome,
            )
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面（fail-open）
            print(f"[notify][warn] run#{rid} {status} 通知失败（fail-open 不阻断）: {type(e).__name__}", file=sys.stderr)

    # ── 派发（7.1）───────────────────────────────────────────────

    def _abandon(self, item: dict, project: str, reason: str) -> None:
        """无 run 的队列放弃收敛点（4b 任务 4.1 + 6.2 归属路由，spec: hub-notifications
        「无 run 放弃告警」）：create_run 前五处 fail_item 的唯一包装——落放弃 +
        notify_abandon（绑定频道进频道，未绑定 admin DM；含 project + 原因摘要）。
        通道关闭（notifier None）stderr 降级留痕；通知异常 fail-open 绝不阻断 fail_item。"""
        self.engine.fail_item(item["id"], reason)
        if self.notifier is None:
            print(f"[notify][warn] 队列放弃（通道关闭，仅 stderr）：item#{item['id']} {project}：{reason}", file=sys.stderr)
            return
        try:
            self.notifier.notify_abandon(project, f"[pa-hub] 队列项 #{item['id']}（{project}）放弃，无 run 产生：\n{reason}")
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

        # add-hub-runner-pool D2（忙闲自动调度，取代 env 固定两键）：派发那一刻
        # 选机——候选 = enabled 且探针绿，负载最低未满载者胜（平局登记序）。
        # 池空/全禁用 = 本机形态（docker_host None + 投影根 data_root，byte-identical）；
        # 池非空但全红/满载 = 本轮不派发（item 留 pending 等下轮——等待安全，不烧
        # 失败、不回落本机：单机红是部署事故须人肉，静默改道只会掩盖）。
        runner_row = None
        if self._db.has_enabled_runners():
            runner_row = self._db.pick_runner_for_dispatch()
            if runner_row is None:
                return False  # pick_next 只读——item 天然留 pending，与「无可派」同径
        docker_host = runner_row["docker_host"] if runner_row else None
        # 投影模型不变（add-hub-remote-runner D2）：hub 侧文件系统操作（必读面检查、
        # mkdir / stamp 防撞 / run_dir 记账）恒走 data_root；仅交付 docker -v 的四路
        # 路径换投影根（daemon 侧解析）。本机形态投影根即 data_root，byte-identical。
        # 固化类 PRD 在 data_root 树外（POST /queue 外部传入）→ 远程形态投影失败
        # abandon fail loud（本机形态照旧直挂原路径）。
        mount_root = Path(runner_row["runner_mount_root"]) if runner_row else self.cfg.data_root
        if runner_row is not None:
            try:
                prd_mount = mount_root / prd.relative_to(self.cfg.data_root)
            except ValueError:
                self._abandon(
                    item, project,
                    f"PRD 不在 HUB_DATA_ROOT 树内，远程形态无法投影挂载源：{prd}"
                    f"（外部传入路径须落 {self.cfg.data_root} 树内后重新入队）",
                )
                return False
        else:
            prd_mount = prd

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
        # 占位符形态检测在 _credentials 内（vault_store.placeholder_issues）——派发前
        # fail-fast abandon，不烧容器才发现（2026-10-23 首跑 #3：占位 env 烧 3 分钟才暴露）。
        # 2b D4：按队列项发起人取凭据；project = 按该项目 forge 声明算必需键（3.2）
        user_env, cred_msg = self._credentials(item["enqueued_by"], project=project)
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

        rid = self._db.create_run(
            item["id"], project, str(run_dir),
            runner_name=runner_row["name"] if runner_row else None,   # per-run 快照（D2）：
            runner_docker_host=docker_host,  # 此后行被改/禁用不影响本 run 对账路由
        )
        # 纯参数（镜像 ENTRYPOINT 已收敛 run_daily.py——docker run tag 后 args 附加到 ENTRYPOINT）
        # --to-stage report：执行链段范围 inject→report（fix-hub-form-parity，spec: execution-hub
        # 「执行链段范围」）——report 段双写产物落 state/reports/ 供面板渲染；run_daily 默认值不动。
        cmd = ["--from-stage", "inject", "--to-stage", "report",
               "--inject-prd", f"/input/{prd.name}", "--stamp", stamp]
        with env_file(user_env) as ef:
            spec = RunSpec(
                # 派生类三挂载源从投影根现算（daemon 侧视角）；hub 侧同名三变量
                # （state_root/profiles_root/sources_file）只做本机检查与记账，不进 RunSpec
                image_tag=item["image_tag"], command=cmd,
                state_root=mount_root / project / "state",
                project=project, prd_input=prd_mount,
                profiles_root=mount_root / project / "profiles",
                sources_file=mount_root / project / "sources.yaml", env_file=ef,
                # 4b 任务 3.3：report 双写出口（docker -e 直传——非凭据，与 env-file 分离）；
                # 落容器内 state 卷（host 可见），run 详情页按 stamp 对齐读。
                # 组5.1 hub 全链 #22：hub 进程 env 的 PA_* 透传（编排器 feature flag 如
                # PA_SINGLE_FLIGHT_AUTO_MERGE 经 hub.env → hub 进程 env → run 容器；破坏性
                # flag 默认关，部署机按需在 hub.env 本地面开，不进 compose/仓）。sorted 保
                # 确定性；PA_REPORT_DIR 本处生成，排除防重复。
                container_env=(f"PA_REPORT_DIR={CONTAINER_STATE}/reports",
                               *(f"{k}={v}" for k, v in sorted(os.environ.items())
                                 if k.startswith("PA_") and k != "PA_REPORT_DIR")),
            )
            try:
                ref = self._runner.start(spec, docker_host=docker_host)
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
        queue_item.prd_path 拿 PRD stem 经 events.locate_pa_artifact 定位
        （精确拼 + 真机 stamp 翻倍 glob 兜底，见其 docstring）。孤儿 run
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
            ev_file = events.locate_pa_artifact(run_dir, stem, ".events.jsonl")
            if ev_file is None:
                return  # 无伴生事件文件（事件未发生或 pa 未写到本 run 目录族）
            for row in events.read_events(ev_file):
                key = (run["id"], row.get("seq"))
                if key in self._event_seen:
                    continue
                self._event_seen.add(key)
                if row.get("event") == "metrics_proposed":
                    # 确认环建行（2.2）：文案须带确认行 #id 才可 confirm——先建行再转发；
                    # 建行不成（无 initiator 等）不 post（无行可确认的清单只会困惑，fail-safe）。
                    rid = self._open_confirmation(run, item, row)
                    if rid is None:
                        continue
                    self.notifier.notify_event(
                        text=_event_text(run, row, rid=rid), enqueued_by=item.get("enqueued_by"),
                        project=run["project"],
                    )
                    continue
                self.notifier.notify_event(
                    text=_event_text(run, row), enqueued_by=item.get("enqueued_by"),
                    project=run["project"],  # 归属路由（6.2）：绑定频道全量进，per-event 判定废止
                )
        except Exception as e:  # 观察面 fail-open——绝不炸 reconcile 主路径
            print(f"[events][warn] run#{run['id']} 事件转发失败（fail-open）: {type(e).__name__}", file=sys.stderr)

    def _open_confirmation(self, run: dict, item: dict, row: dict) -> int | None:
        """metrics_proposed → confirmation_requests 建行（2.2，spec: 指标清单回流）。

        字段映射（spec）：initiator=enqueued_by（入队会话用户 = mm_user_id）、
        channel_id=建行时刻项目绑定快照（未绑定存 ""，confirm 实时查绑定为判据）、
        prd_text=item prd_path 全文快照（confirm 并节的原料）、metrics_json=事件
        metrics、origin_sha=祖先链根（同 project+发起人有前行则继承——confirm/metrics
        重入队后新 prd_sha 不换根；首次 = 事件 prd_sha）。
        无 initiator / PRD 读失败 → 不建行 + warn（回归信号）；事件重复到达幂等
        （idx_confirm_item UNIQUE，已存在回查既有 rid——hub 重启重发语义）。
        fail-open：任一异常 stderr 告警不抛（reconcile 同步路径，观察面纪律）。
        """
        try:
            initiator = (item.get("enqueued_by") or "").strip()
            if not initiator:
                print(f"[confirm][warn] run#{run['id']} metrics_proposed 无 initiator"
                      "（enqueued_by 缺）——不建行", file=sys.stderr)
                return None
            try:
                prd_text = Path(item["prd_path"]).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"[confirm][warn] run#{run['id']} PRD 快照读失败——不建行: {type(e).__name__}",
                      file=sys.stderr)
                return None
            binding = self._db.get_channel_binding_by_project(run["project"])
            origin = (self._db.latest_confirmation_origin(run["project"], initiator)
                      or str(row.get("prd_sha") or ""))
            rid = self._db.insert_confirmation(
                item_id=run["queue_item_id"], project=run["project"],
                channel_id=binding["channel_id"] if binding else "",
                initiator=initiator, prd_text=prd_text,
                metrics_json=json.dumps(row.get("metrics") or [], ensure_ascii=False),
                origin_sha=origin)
            if rid is not None:
                return rid
            existing = self._db.get_confirmation_by_item(run["queue_item_id"])
            return existing["id"] if existing else None
        except Exception as e:  # noqa: BLE001——建行挂点在 reconcile 同步路径，fail-open
            print(f"[confirm][warn] run#{run['id']} 建行失败（fail-open）: {type(e).__name__}",
                  file=sys.stderr)
            return None


    def sweep_confirmations(self) -> None:
        """确认环超时扫描（2.4，spec: 确认超时）：requested >72h → expired + 回执
        （notify_event 三代分流 @发起人）。观察面 fail-open；notifier 关闭仅落库
        静默（对齐放弃告警纪律——超时是机械事实，通道关不阻断置态）。"""
        try:
            swept = self._db.expire_sweep_confirmations()
        except Exception as e:  # noqa: BLE001——观察面挂点，绝不炸 reconcile tick
            print(f"[confirm][warn] 超时扫描异常（fail-open）: {type(e).__name__}", file=sys.stderr)
            return
        for row in swept:
            if self.notifier is None:
                continue
            try:
                self.notifier.notify_event(
                    text=(f"[pa-hub] 确认行 #{row['id']}（{row['project']}）超 72h 未决——已过期；"
                          "如仍需要请 `/pa run` 重新提交（expired 不可再 confirm）"),
                    enqueued_by=row["initiator"], project=row["project"])
            except Exception as e:  # noqa: BLE001
                print(f"[confirm][warn] 过期回执失败（fail-open）: {type(e).__name__}", file=sys.stderr)

    def probe_runners(self) -> None:
        """定期健康探针挂点（add-hub-runner-pool 4.1，daemon tick N 周期调用）：
        逐 enabled 行三项探针（daemon 可达 / 镜像在场 / 双挂回读），结果写 runners
        表行缓存（绿 = 调度候选必要条件）。fail-open——内部逐行吞异常，观察面
        绝不崩排度循环（与 sweep_confirmations 同纪律）。"""
        health_probe.probe_enabled_runners(self._db, self.cfg)

    def reconcile_running(self) -> None:
        """轮询 running run 行：exited → 记终态走状态机；gone → 按卷证据归档。"""
        for run in self._db.runs_in_status("running"):
            # 对账按行内快照路由（add-hub-runner-pool D2）：runner 行此后被改/被禁用
            # 不断账——NULL（旧行/本机形态）= 本机 daemon。
            st = self._runner.status(run["container_ref"], docker_host=run["runner_docker_host"])
            if st.state == "running":
                self._forward_events(run)  # 4b：running 中读伴生事件文件转发新行（观察面）
                continue
            if st.state == "exited":
                rc = st.exit_code if st.exit_code is not None else 1  # None 不该出现，兜底按失败
                status = "succeeded" if rc == 0 else "failed"
                self._forward_events(run)  # 终态 flush（2.4）：末轮 running 后新写行不丢（metrics_proposed 丢 = 确认环死链）
                self._finish_run(run["id"], status, exit_code=rc, project=run["project"],
                                 run_dir_name=Path(run["run_dir"]).name,
                                 item=self._safe_item(run["queue_item_id"]))
                self.engine.on_exit(run["queue_item_id"], rc)
            else:  # gone——终态不明，以卷上 journal 证据归档，绝不静默当成功
                has_journal = _journal_exists(run["run_dir"])
                status = "failed" if has_journal else "failed_to_start"
                self._forward_events(run)  # 终态 flush（2.4）：卷在事件文件就可能在——flush 先于归档
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
