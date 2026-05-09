"""
Pipeline — End-to-end closed-loop orchestrator.

v2: Upgraded to competitive batch feed ranking.

Pipeline stages per cycle:
    Agent (strategy_engine + lifecycle_engine)
        ↓  ActionPlan [role, intent, niche, intensity]
    Feed Engine — rank_batch() (competitive, Part 1)
        ↓  FeedResult [reach, virality, ranking, position, viral_state, attention]
    Engagement Simulator
        ↓  EngagementResult [like, comment, share, skip, engagement_score]
    Detector Simulator
        ↓  DetectionResult [risk_score, flags]
    Metrics Store → RL Policy → Optimizer
    Lifecycle Engine (interest evolution)

Architecture contracts:
  - One cycle = one simulated time step (default: 1 hour).
  - Individual account failures are isolated (exception-safe).
  - No cross-account state mutation.
  - Output: PipelineCycleReport (JSON-serialisable).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.pipeline")


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class AccountCycleResult:
    """Full per-account result for one pipeline cycle."""
    account_id:       str
    plan_role:        str    = "IDLE"
    plan_intent:      str    = "none"
    plan_niche:       str    = "unknown"
    plan_intensity:   float  = 0.0
    plan_skipped:     bool   = False
    reach_score:      float  = 0.0
    virality_score:   float  = 0.0
    ranking_score:    float  = 0.0
    feed_flags:       list[str] = field(default_factory=list)
    engagement_score: float  = 0.0
    like_rate:        float  = 0.0
    comment_rate:     float  = 0.0
    share_rate:       float  = 0.0
    is_viral:         bool   = False
    is_suppressed:    bool   = False
    detection_risk:   float  = 0.0
    detection_flags:  list[str] = field(default_factory=list)
    success:          bool   = False
    ban:              bool   = False
    reward:           float  = 0.0
    lifecycle_stage:  str    = "GROWTH"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class PipelineCycleReport:
    """Aggregated report for one full fleet pipeline cycle."""
    cycle:               int
    now:                 int
    platform:            str
    n_accounts:          int
    n_active:            int
    n_skipped:           int
    n_success:           int
    n_ban:               int
    n_viral:             int
    n_suppressed:        int
    avg_engagement:      float
    avg_detection_risk:  float
    avg_ranking:         float
    role_distribution:   dict[str, int]
    niche_distribution:  dict[str, int]
    fleet_health:        float
    optimizer_state:     dict[str, float]
    accounts:            list[AccountCycleResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "accounts"}
        d["accounts"] = [a.to_dict() for a in self.accounts]
        return d

    def summary(self) -> str:
        return (
            f"Cycle {self.cycle:03d} | platform={self.platform} | "
            f"active={self.n_active}/{self.n_accounts} | "
            f"success={self.n_success} ban={self.n_ban} viral={self.n_viral} | "
            f"engagement={self.avg_engagement:.3f} detection={self.avg_detection_risk:.3f} | "
            f"health={self.fleet_health:.3f}"
        )


# ── Pipeline ──────────────────────────────────────────────────────────────────

class Pipeline:
    """
    End-to-end simulation pipeline for a fleet of accounts.

    Execution order per cycle:
      Phase 1 — Agent planning for all accounts (collect ContentPosts)
      Phase 2 — rank_batch(all_posts) — competitive feed ranking (Part 1 v2)
      Phase 3 — Per-account: engagement → observer → detector → RL → lifecycle
      Phase 4 — Fleet-wide optimizer update
    """

    def __init__(
        self,
        accounts:     list[str],
        platform:     str = "tiktok",
        created_ts:   int = 0,
        cycle_step_s: int = 3600,
    ) -> None:
        self.accounts     = accounts
        self.platform     = platform
        self.created_ts   = created_ts or (int(time.time()) - 30 * 86400)
        self.cycle_step_s = cycle_step_s
        self._cycle       = 0

    # ── Single-cycle execution ─────────────────────────────────────────────────

    def run_cycle(self, now: int | None = None) -> PipelineCycleReport:
        """Execute one full pipeline cycle for all accounts."""
        if now is None:
            now = int(time.time())

        cycle = self._cycle
        self._cycle += 1

        # ── Import modules (lazy, exception-safe) ──────────────────────────────
        try:
            from core.strategy_engine import plan_actions, record_outcome as se_record
            from core.feed_engine import ContentPost, rank_batch
            from core.engagement_simulator import simulate_engagement, outcome_from_engagement
            from core.detector_simulator import get_detector, get_risk_score
            from core.metrics_store import get_metrics_store
            from core.observer import get_observer, ModifierSnapshot
            from core.reinforcement import get_policy, build_state
            from core.optimizer import get_optimizer
            from core.lifecycle_engine import (
                get_lifecycle_stage, evolve_interests,
            )
        except Exception as exc:
            LOGGER.error("pipeline_import_error cycle=%d error=%s", cycle, exc)
            return self._empty_report(cycle, now)

        store  = get_metrics_store()
        obs    = get_observer()
        det    = get_detector()
        policy = get_policy()
        optim  = get_optimizer()

        # ── Phase 1: Agent Planning ────────────────────────────────────────────
        # Collect ContentPosts for all accounts before feed ranking.
        # rank_batch() needs all posts simultaneously for competitive scoring.

        account_plan_data: list[dict] = []

        for account_id in self.accounts:
            result = AccountCycleResult(account_id=account_id)

            try:
                plan = plan_actions(
                    account_id = account_id,
                    platform   = self.platform,
                    created_ts = self.created_ts,
                    now        = now,
                    risk_score = get_risk_score(account_id),
                )
            except Exception as exc:
                LOGGER.warning("pipeline_plan_error account=%s error=%s", account_id, exc)
                plan = None

            # Part 2: strategy_risk_gate — check current account detection risk
            if plan is not None:
                try:
                    from core.adversarial_engine import strategy_risk_gate
                    current_risk = get_risk_score(account_id)
                    allowed, gate_reason = strategy_risk_gate(current_risk, plan.role.value)
                    if not allowed:
                        LOGGER.debug("pipeline_risk_gate account=%s reason=%s", account_id, gate_reason)
                        plan = None   # hard stop
                    elif "restricted" in gate_reason and plan.role.value == "AMPLIFIER":
                        # Halve intensity for restricted AMPLIFIER
                        try:
                            from dataclasses import replace as dc_replace
                            plan = dc_replace(plan, intensity=max(0.1, plan.intensity * 0.5))
                        except Exception:
                            pass
                except Exception:
                    pass   # gate failures are non-fatal

            if plan is None:
                result.plan_skipped = True
                account_plan_data.append({
                    "account_id": account_id, "result": result,
                    "plan": None, "feed_post": None, "stage": "GROWTH",
                })
                continue

            result.plan_role      = plan.role.value
            result.plan_intent    = plan.intent_type.value
            result.plan_niche     = plan.niche
            result.plan_intensity = plan.intensity

            lifecycle_stage_str = "GROWTH"
            try:
                lc_stage = get_lifecycle_stage(account_id, self.created_ts, now)
                lifecycle_stage_str = lc_stage.value
                result.lifecycle_stage = lifecycle_stage_str
            except Exception:
                pass

            # ── DARWIN: scale intensity by cluster resource share (Part 4) ──
            darwin_intensity = plan.intensity
            try:
                from core.account_clustering import get_cluster_id as _get_cid
                from core.swarm_dynamics import (
                    apply_resource_to_intensity, is_cluster_inactive,
                    get_mutation_boost,
                )
                _acct_cluster = _get_cid(account_id)
                if is_cluster_inactive(_acct_cluster):
                    # Inactive cluster → no actions
                    result.plan_skipped = True
                    account_plan_data.append({
                        "account_id": account_id, "result": result,
                        "plan": None, "feed_post": None, "stage": lifecycle_stage_str,
                    })
                    continue
                darwin_intensity = apply_resource_to_intensity(_acct_cluster, plan.intensity)
                result.plan_intensity = darwin_intensity
            except Exception:
                _acct_cluster = "c0"   # safe fallback

            feed_post = ContentPost(
                account_id      = account_id,
                platform        = self.platform,
                niche           = plan.niche,
                intensity       = darwin_intensity,
                lifecycle_stage = lifecycle_stage_str,
                created_ts      = self.created_ts,
                now             = now,
            )
            account_plan_data.append({
                "account_id":   account_id,
                "result":       result,
                "plan":         plan,
                "feed_post":    feed_post,
                "stage":        lifecycle_stage_str,
                "cluster_id":   _acct_cluster,
            })

        # ── Phase 2: Competitive Batch Feed Ranking ────────────────────────────
        # rank_batch applies: competition density, attention budget, viral cascade,
        # feed position effects, and creator exposure limits (Parts 1-5 v2).

        active_plan_data = [d for d in account_plan_data if d["plan"] is not None]
        active_posts     = [d["feed_post"] for d in active_plan_data]
        feed_results_map: dict[str, Any] = {}

        try:
            if active_posts:
                batch_results = rank_batch(active_posts)
                for d, fr in zip(active_plan_data, batch_results):
                    feed_results_map[d["account_id"]] = fr
        except Exception as exc:
            LOGGER.warning("pipeline_batch_rank_error cycle=%d error=%s", cycle, exc)

        # ── Phase 3: Per-account Engagement + Downstream ──────────────────────

        account_results:       list[AccountCycleResult] = []
        fleet_detection_risks: list[float]              = []
        observer_logs:         dict[str, list[dict]]    = {}

        for entry in account_plan_data:
            account_id          = entry["account_id"]
            result              = entry["result"]
            plan                = entry["plan"]
            feed_post           = entry["feed_post"]
            lifecycle_stage_str = entry["stage"]

            if plan is None or result.plan_skipped:
                account_results.append(result)
                continue

            feed_result = feed_results_map.get(account_id)
            if feed_result is None:
                result.plan_skipped = True
                account_results.append(result)
                continue

            # ── DARWIN: scale reach by cluster resource share (Part 4) ─────
            darwin_reach = feed_result.reach_score
            try:
                from core.swarm_dynamics import apply_resource_to_reach
                _cid_for_reach = entry.get("cluster_id", "c0")
                darwin_reach = apply_resource_to_reach(_cid_for_reach, feed_result.reach_score)
                feed_result.reach_score = darwin_reach
            except Exception:
                pass

            result.reach_score    = feed_result.reach_score
            result.virality_score = feed_result.virality_score
            result.ranking_score  = feed_result.ranking_score
            result.feed_flags     = list(feed_result.flags.keys())

            # Stage 3: Engagement Simulator
            try:
                eng = simulate_engagement(feed_result, feed_post)
                result.engagement_score = eng.engagement_score
                result.like_rate        = eng.like_rate
                result.comment_rate     = eng.comment_rate
                result.share_rate       = eng.share_rate
                result.is_viral         = eng.is_viral
                result.is_suppressed    = eng.is_suppressed
                success, ban            = outcome_from_engagement(eng)
            except Exception as exc:
                LOGGER.warning("pipeline_eng_error account=%s error=%s", account_id, exc)
                eng, success, ban = None, False, False

            result.success = success
            result.ban     = ban

            # Stage 4a: Observer
            try:
                mods = ModifierSnapshot(
                    role               = plan.role.value,
                    platform           = self.platform,
                    strategy_intensity = plan.intensity,
                    timing_offset_s    = plan.timing_offset,
                    ban_rate           = store.get_ema("ban_rate"),
                )
                obs_log = obs.record_plan(
                    account_id = account_id,
                    platform   = self.platform,
                    role       = plan.role.value,
                    intent     = plan.intent_type.value,
                    delay_s    = plan.timing_offset,
                    niche      = plan.niche,
                    modifiers  = mods,
                )
                obs.record_outcome(obs_log, success=success, ban=ban,
                                   anomaly_score=0.1 if ban else 0.0)
                observer_logs[account_id] = obs.replay(account_id)
            except Exception as exc:
                LOGGER.debug("pipeline_obs_error account=%s error=%s", account_id, exc)

            # Stage 4b: Detector Simulator
            try:
                det_result = det.evaluate(
                    account_id  = account_id,
                    now         = now,
                    created_ts  = self.created_ts,
                    logs        = observer_logs.get(account_id, []),
                )
                result.detection_risk  = det_result.risk_score
                result.detection_flags = list(det_result.flags.keys())
                fleet_detection_risks.append(det_result.risk_score)
                try:
                    from core.detector_simulator import record_to_metrics
                    record_to_metrics(det_result)
                except Exception:
                    pass
            except Exception as exc:
                LOGGER.debug("pipeline_det_error account=%s error=%s", account_id, exc)

            # Stage 5: Metrics Store
            try:
                if ban:
                    store.record_ban(account_id)
                elif success and eng is not None:
                    store.record_success(account_id, engagement=eng.engagement_score)
                if eng is not None:
                    store.update("engagement_score", eng.engagement_score, tag=account_id)
            except Exception:
                pass

            # Stage 6: RL Update
            risk_tol = 0.5
            try:
                from core.persona_engine import get_persona_engine
                risk_tol = get_persona_engine().get(account_id).risk_tolerance
            except Exception:
                pass

            try:
                rl_state = build_state(
                    role           = plan.role.value,
                    platform       = self.platform,
                    risk_tolerance = risk_tol,
                    intent         = plan.intent_type.value,
                )
                rl_out = policy.select_action(rl_state, now=now)
                reward = policy.update(
                    state_key           = rl_out.state_key,
                    action              = rl_out.action,
                    success             = success,
                    ban                 = ban,
                    anomaly_score       = result.detection_risk * 0.5,
                    role                = plan.role.value,
                    niche               = plan.niche,
                    lifecycle_stage     = lifecycle_stage_str,
                    created_ts          = self.created_ts,
                    account_id          = account_id,
                    detector_risk_score = result.detection_risk,
                    now                 = now,
                )
                result.reward = round(reward, 4)
            except Exception as exc:
                LOGGER.debug("pipeline_rl_error account=%s error=%s", account_id, exc)

            try:
                trend_val = store.get_ema("engagement_score")
                evolve_interests(
                    account_id = account_id,
                    now        = now,
                    feedback   = {
                        "success":         success,
                        "ban":             ban,
                        "niche":           plan.niche,
                        "trend_intensity": trend_val,
                    },
                    created_ts = self.created_ts,
                )
            except Exception as exc:
                LOGGER.debug("pipeline_lc_evolve_error account=%s error=%s", account_id, exc)

            # Stage 8: Strategy Engine outcome feedback
            try:
                se_record(
                    account_id  = account_id,
                    role        = plan.role,
                    intent_type = plan.intent_type,
                    success     = success,
                    ban         = ban,
                )
            except Exception as exc:
                LOGGER.debug("pipeline_se_record_error account=%s error=%s", account_id, exc)

            # Stage 9: Update account embedding for dynamic clustering
            try:
                from core.account_clustering import update_embedding
                update_embedding(
                    account_id      = account_id,
                    detection_risk  = result.detection_risk,
                    reward          = result.reward,
                    success         = result.success,
                    lifecycle_stage = result.lifecycle_stage,
                    is_suppressed   = result.is_suppressed,
                )
            except Exception:
                pass   # clustering is advisory; never blocks the pipeline

            account_results.append(result)

        # ── Phase 4: Fleet-wide Optimizer + Per-cluster Adaptation ───────────
        optimizer_state: dict[str, float] = {}
        try:
            avg_det = (
                sum(fleet_detection_risks) / len(fleet_detection_risks)
                if fleet_detection_risks else 0.0
            )

            # ── 4a: Global fleet-level update (fallback + adversarial hooks) ──
            optimizer_state = optim.update(
                ban_rate            = store.get_ema("ban_rate"),
                success_rate        = store.get_ema("success_rate"),
                anomaly_score       = store.get_ema("anomaly_score"),
                health_score        = store.health_score(),
                spike_flag          = False,
                detector_risk_score = avg_det,
            )

            # ── 4b: Adversarial + fleet pressure (unchanged) ──────────────────
            try:
                from core.adversarial_engine import (
                    optimizer_risk_feedback, update_fleet_pressure,
                    parse_risk_components,
                )
                fleet_risk_components: dict[str, list[float]] = {}
                for acct_r in account_results:
                    if acct_r.plan_skipped:
                        continue
                    try:
                        obs_logs = observer_logs.get(acct_r.account_id, [])
                        if obs_logs:
                            dr = det.evaluate(
                                account_id = acct_r.account_id,
                                now        = now,
                                created_ts = self.created_ts,
                                logs       = obs_logs,
                            )
                            for k, v in dr.sub_scores.items():
                                fleet_risk_components.setdefault(k, []).append(v)
                    except Exception:
                        pass

                avg_components = {
                    k: sum(vs) / len(vs)
                    for k, vs in fleet_risk_components.items()
                    if vs
                }
                optimizer_risk_feedback(optim, avg_det, avg_components)
                update_fleet_pressure(
                    fleet_id   = self.platform,
                    risk_score = avg_det,
                    optimizer  = optim,
                )
            except Exception as adv_exc:
                LOGGER.debug("pipeline_adversarial_error cycle=%d error=%s", cycle, adv_exc)

            # ── 4c: Per-cluster optimizer update + meta-learning ──────────────
            try:
                from core.meta_learning import (
                    AccountSignal, cluster_meta_record,
                    partial_reset as meta_partial_reset,
                    _risk_bucket, _activity_level,
                )
                from core.account_clustering import (
                    notify_cycle as ac_notify_cycle,
                    get_cluster_id,
                    record_cluster_quality,
                )

                # Trigger re-clustering every N_RECLUSTER cycles
                ac_notify_cycle(cycle)

                # Build per-account signals and group into clusters
                cluster_signals: dict[str, list[AccountSignal]] = {}
                cluster_risk:    dict[str, list[float]]         = {}
                cluster_success: dict[str, list[float]]         = {}
                cluster_anomaly: dict[str, list[float]]         = {}
                cluster_niches:  dict[str, list[str]]           = {}   # ECOSYSTEM: niche tracking
                total_reward  = 0.0
                n_active_meta = 0

                for r in account_results:
                    if r.plan_skipped:
                        continue
                    # Dynamic cluster_id from embedding-based k-means
                    ck  = get_cluster_id(r.account_id)
                    # Derive AccountSignal metadata from cluster_id + result
                    rb  = _risk_bucket(optimizer_state)
                    al  = _activity_level(r.detection_risk, r.reward)
                    sig = AccountSignal(
                        risk_bucket     = rb,
                        lifecycle_stage = r.lifecycle_stage or "unknown",
                        activity_level  = al,
                        reward          = r.reward,
                    )
                    cluster_signals.setdefault(ck, []).append(sig)
                    cluster_risk.setdefault(ck, []).append(r.detection_risk)
                    cluster_success.setdefault(ck, []).append(1.0 if r.success else 0.0)
                    cluster_anomaly.setdefault(ck, []).append(r.detection_risk * 0.5)
                    cluster_niches.setdefault(ck, []).append(r.plan_niche or "unknown")
                    total_reward  += r.reward
                    n_active_meta += 1

                # Per-cluster optimizer update + meta record
                # Also accumulate data for Darwin cycle
                _darwin_rewards: dict[str, float] = {}
                _darwin_risks:   dict[str, float] = {}
                _darwin_sizes:   dict[str, int]   = {}

                for ck, sigs in cluster_signals.items():
                    c_det     = sum(cluster_risk[ck])    / len(cluster_risk[ck])
                    c_success = sum(cluster_success[ck]) / len(cluster_success[ck])
                    c_anomaly = sum(cluster_anomaly[ck]) / len(cluster_anomaly[ck])
                    c_reward  = sum(s.reward for s in sigs) / len(sigs)

                    # Feed quality signal into swarm (reward / (1+risk))
                    try:
                        c_idx = int(ck[1:])
                        record_cluster_quality(c_idx, c_det, c_reward)
                    except Exception:
                        pass

                    # Accumulate for Darwin layer
                    _darwin_rewards[ck] = c_reward
                    _darwin_risks[ck]   = c_det
                    _darwin_sizes[ck]   = len(sigs)

                    cluster_params = optim.update_for_cluster(
                        cluster_key         = ck,
                        ban_rate            = store.get_ema("ban_rate"),
                        success_rate        = c_success,
                        anomaly_score       = c_anomaly,
                        health_score        = store.health_score(),
                        spike_flag          = False,
                        detector_risk_score = c_det,
                    )

                    # GENOME PART 2 -- record optimizer output as cluster genome
                    try:
                        from core.swarm_dynamics import record_cluster_genome, update_cluster_niche
                        record_cluster_genome(ck, cluster_params, risk=c_det)
                        # ECOSYSTEM Part 1: niche derived from genome internally
                        update_cluster_niche(ck)
                    except Exception:
                        pass

                    # Meta-record using cluster-specific params (not global)
                    meta_phase = (
                        "peak"
                        if cluster_params.get("platform_burstiness_mult", 1.0) > 1.05
                        else "offpeak"
                    )
                    cluster_meta_record(cluster_params, sigs, platform_phase=meta_phase)

                # ── DARWIN: run full evolutionary selection cycle (Part 3) ──
                try:
                    from core.swarm_dynamics import run_darwin_cycle
                    darwin_summary = run_darwin_cycle(
                        cluster_rewards = _darwin_rewards,
                        cluster_risks   = _darwin_risks,
                        cluster_sizes   = _darwin_sizes,
                    )
                    if darwin_summary["eliminated"]:
                        LOGGER.info(
                            "pipeline_darwin eliminated=%s cycle=%d",
                            darwin_summary["eliminated"], cycle,
                        )
                    if darwin_summary["reproduced"]:
                        LOGGER.info(
                            "pipeline_darwin reproduced=%s cycle=%d",
                            darwin_summary["reproduced"], cycle,
                        )
                except Exception as darwin_exc:
                    LOGGER.debug("pipeline_darwin_error cycle=%d error=%s", cycle, darwin_exc)

                # Distribution-shift guard (fleet-level)
                fleet_avg_reward = total_reward / max(1, n_active_meta)
                meta_partial_reset(avg_det, fleet_avg_reward)

            except Exception as meta_exc:
                LOGGER.debug("pipeline_meta_error cycle=%d error=%s", cycle, meta_exc)

        except Exception as exc:
            LOGGER.warning("pipeline_optimizer_error cycle=%d error=%s", cycle, exc)


        # ── Build cycle report ─────────────────────────────────────────────────
        active   = [r for r in account_results if not r.plan_skipped]
        n_active = len(active)

        roles:  dict[str, int] = {}
        niches: dict[str, int] = {}
        for r in active:
            roles[r.plan_role]   = roles.get(r.plan_role, 0) + 1
            niches[r.plan_niche] = niches.get(r.plan_niche, 0) + 1

        avg_eng  = sum(r.engagement_score for r in active) / max(1, n_active)
        avg_det  = sum(r.detection_risk   for r in active) / max(1, n_active)
        avg_rank = sum(r.ranking_score    for r in active) / max(1, n_active)

        try:
            health = store.health_score()
        except Exception:
            health = 0.5

        report = PipelineCycleReport(
            cycle              = cycle,
            now                = now,
            platform           = self.platform,
            n_accounts         = len(self.accounts),
            n_active           = n_active,
            n_skipped          = len(self.accounts) - n_active,
            n_success          = sum(1 for r in active if r.success),
            n_ban              = sum(1 for r in active if r.ban),
            n_viral            = sum(1 for r in active if r.is_viral),
            n_suppressed       = sum(1 for r in active if r.is_suppressed),
            avg_engagement     = round(avg_eng,  4),
            avg_detection_risk = round(avg_det,  4),
            avg_ranking        = round(avg_rank, 4),
            role_distribution  = roles,
            niche_distribution = niches,
            fleet_health       = round(health, 4),
            optimizer_state    = optimizer_state,
            accounts           = account_results,
        )

        LOGGER.info("pipeline_cycle %s", report.summary())
        return report

    # ── Multi-cycle run ────────────────────────────────────────────────────────

    def run(
        self,
        n_cycles:    int = 24,
        base_ts:     int | None = None,
        output_path: str | None = None,
    ) -> list[PipelineCycleReport]:
        """Run N full pipeline cycles (default: 24 hours)."""
        now     = base_ts or int(time.time())
        reports: list[PipelineCycleReport] = []

        for i in range(n_cycles):
            report = self.run_cycle(now=now + i * self.cycle_step_s)
            reports.append(report)

        if output_path:
            try:
                Path(output_path).write_text(
                    json.dumps([r.to_dict() for r in reports], indent=2),
                    encoding="utf-8",
                )
                LOGGER.info("pipeline_report_written path=%s cycles=%d", output_path, n_cycles)
            except Exception as exc:
                LOGGER.warning("pipeline_report_write_error %s", exc)

        return reports

    def _empty_report(self, cycle: int, now: int) -> PipelineCycleReport:
        return PipelineCycleReport(
            cycle=cycle, now=now, platform=self.platform,
            n_accounts=len(self.accounts), n_active=0, n_skipped=len(self.accounts),
            n_success=0, n_ban=0, n_viral=0, n_suppressed=0,
            avg_engagement=0.0, avg_detection_risk=0.0, avg_ranking=0.0,
            role_distribution={}, niche_distribution={},
            fleet_health=0.5, optimizer_state={},
        )


# ── Convenience factory ───────────────────────────────────────────────────────

def create_pipeline(
    n_accounts:      int = 50,
    platform:        str = "tiktok",
    account_age_days: int = 30,
    cycle_step_s:    int = 3600,
) -> Pipeline:
    """Create a pipeline with N synthetic accounts."""
    return Pipeline(
        accounts     = [f"pipeline-acct-{i:04d}" for i in range(n_accounts)],
        platform     = platform,
        created_ts   = int(time.time()) - account_age_days * 86400,
        cycle_step_s = cycle_step_s,
    )
