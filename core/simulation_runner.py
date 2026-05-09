"""
Simulation Runner — End-to-end closed-loop simulation for N accounts over T cycles.

Pipeline per cycle:
  plan → observe → execute (simulated) → validate → metrics → optimize → RL update

Output: simulation_report.json
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.simulation_runner")

# Simulated ban probability per role (for outcome generation)
_ROLE_BAN_PROB: dict[str, float] = {
    "WARMER":    0.01,
    "EXPLORER":  0.03,
    "AMPLIFIER": 0.05,
    "HARVESTER": 0.08,
    "IDLE":      0.00,
}
_ROLE_SUCCESS_PROB: dict[str, float] = {
    "WARMER":    0.70,
    "EXPLORER":  0.55,
    "AMPLIFIER": 0.65,
    "HARVESTER": 0.80,
    "IDLE":      0.20,
}


@dataclass
class CycleStats:
    cycle:             int
    accounts_active:   int
    accounts_skipped:  int
    role_distribution: dict[str, int]
    ban_count:         int
    success_count:     int
    timing_entropy:    float
    action_diversity:  float
    collision_rate:    float
    health_score:      float
    avg_reward:        float


@dataclass
class SimulationReport:
    n_accounts:    int
    n_cycles:      int
    platform:      str
    cycles:        list[CycleStats] = field(default_factory=list)
    final_metrics: dict[str, Any]  = field(default_factory=dict)
    final_q_snap:  dict[str, Any]  = field(default_factory=dict)
    optimizer_snap: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_accounts":    self.n_accounts,
            "n_cycles":      self.n_cycles,
            "platform":      self.platform,
            "cycles":        [
                {
                    "cycle":             c.cycle,
                    "accounts_active":   c.accounts_active,
                    "accounts_skipped":  c.accounts_skipped,
                    "role_distribution": c.role_distribution,
                    "ban_count":         c.ban_count,
                    "success_count":     c.success_count,
                    "timing_entropy":    round(c.timing_entropy,  4),
                    "action_diversity":  round(c.action_diversity, 4),
                    "collision_rate":    round(c.collision_rate,  4),
                    "health_score":      round(c.health_score,    4),
                    "avg_reward":        round(c.avg_reward,      4),
                }
                for c in self.cycles
            ],
            "final_metrics":  self.final_metrics,
            "final_q_snap":   self.final_q_snap,
            "optimizer_snap": self.optimizer_snap,
        }


def _simulated_outcome(role: str, seed: int) -> tuple[bool, bool]:
    """Deterministic outcome: (success, ban) based on role + seed."""
    from core.mutation_controller import stable_hash_int
    s = stable_hash_int("sim", role, str(seed)) % 100
    ban     = s < int(_ROLE_BAN_PROB.get(role, 0.03) * 100)
    success = (not ban) and s < int(_ROLE_SUCCESS_PROB.get(role, 0.5) * 100)
    return success, ban


def _timing_entropy(offsets: list[int]) -> float:
    """Shannon entropy of timing offset distribution."""
    if not offsets:
        return 0.0
    from collections import Counter
    counts = Counter(offsets)
    n = len(offsets)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _action_diversity(plans: list[Any]) -> float:
    """Fraction of unique (role, intent, niche) tuples."""
    if not plans:
        return 0.0
    fps = set((p.role.value, p.intent_type.value, p.niche) for p in plans)
    return len(fps) / len(plans)


def _collision_rate(offsets: list[int]) -> float:
    """Fraction of offsets that are non-unique."""
    if len(offsets) < 2:
        return 0.0
    from collections import Counter
    counts = Counter(offsets)
    non_unique = sum(c for c in counts.values() if c > 1)
    return non_unique / len(offsets)


def run_simulation(
    n_accounts: int = 50,
    n_cycles:   int = 10,
    platform:   str = "tiktok",
    base_ts:    int | None = None,
    cycle_step_s: int = 3600,
    output_path:  str = "simulation_report.json",
) -> SimulationReport:
    """
    Simulate N accounts for T cycles with the full closed-loop pipeline.

    Each cycle is 1 hour (cycle_step_s) of simulated time.
    """
    from core.strategy_engine import plan_actions, record_outcome as se_record
    from core.observer import get_observer, ModifierSnapshot, reset_observer
    from core.metrics_store import get_metrics_store, reset_metrics_store
    from core.validator import validate_fleet
    from core.optimizer import get_optimizer, reset_optimizer
    from core.reinforcement import get_policy, build_state, reset_policy

    # Reset all singletons for clean simulation
    reset_observer()
    reset_metrics_store()
    reset_optimizer()
    reset_policy()

    from core.strategy_engine import _reset_for_testing as se_reset
    se_reset()

    try:
        from core.persona_engine import reset_persona_engine
        reset_persona_engine()
    except Exception:
        pass

    obs    = get_observer()
    store  = get_metrics_store()
    optim  = get_optimizer()
    policy = get_policy()

    now = base_ts or int(time.time())
    accounts = [f"sim-acct-{i:04d}" for i in range(n_accounts)]
    created_ts = now - 30 * 86400   # all accounts 30 days old (veterans)

    report = SimulationReport(n_accounts=n_accounts, n_cycles=n_cycles, platform=platform)

    for cycle in range(n_cycles):
        cycle_ts = now + cycle * cycle_step_s
        plans   = []
        offsets = []
        role_counts: dict[str, int] = {}
        ban_count = success_count = 0

        # ── Plan ──────────────────────────────────────────────────────────────
        account_logs: dict[str, list[dict]] = {}

        for acct in accounts:
            plan = plan_actions(
                account_id = acct,
                platform   = platform,
                created_ts = created_ts,
                now        = cycle_ts,
                risk_score = 0.1,
            )
            if plan is None:
                continue

            plans.append(plan)
            offsets.append(plan.timing_offset)
            role_counts[plan.role.value] = role_counts.get(plan.role.value, 0) + 1

            # ── Observe ───────────────────────────────────────────────────────
            mods = ModifierSnapshot(
                role               = plan.role.value,
                platform           = platform,
                strategy_intensity = plan.intensity,
                timing_offset_s    = plan.timing_offset,
            )
            log = obs.record_plan(
                account_id = acct,
                platform   = platform,
                role       = plan.role.value,
                intent     = plan.intent_type.value,
                delay_s    = plan.timing_offset,
                niche      = plan.niche,
                modifiers  = mods,
            )

            # ── Simulated execution outcome ───────────────────────────────────
            seed    = cycle * n_accounts + accounts.index(acct)
            success, ban = _simulated_outcome(plan.role.value, seed)
            obs.record_outcome(log, success=success, ban=ban)

            # ── Metrics store ─────────────────────────────────────────────────
            if ban:
                store.record_ban(acct)
                ban_count += 1
            elif success:
                store.record_success(acct, engagement=plan.intensity)
                success_count += 1

            # ── Strategy engine feedback ──────────────────────────────────────
            se_record(
                account_id = acct,
                role       = plan.role,
                intent_type= plan.intent_type,
                success    = success,
                ban        = ban,
            )

            # ── RL update ─────────────────────────────────────────────────────
            try:
                from core.persona_engine import get_persona_engine
                pe = get_persona_engine()
                persona = pe.get(acct)
                risk_tol = persona.risk_tolerance
            except Exception:
                risk_tol = 0.5

            state = build_state(
                role           = plan.role.value,
                platform       = platform,
                risk_tolerance = risk_tol,
                intent         = plan.intent_type.value,
            )
            rl_out = policy.select_action(state, now=cycle_ts)
            policy.update(
                state_key     = rl_out.state_key,
                action        = rl_out.action,
                success       = success,
                ban           = ban,
                anomaly_score = 0.05 if ban else 0.0,
            )

            # Collect logs for validation
            account_logs.setdefault(acct, []).append(log.to_dict())

        # ── Validate fleet ────────────────────────────────────────────────────
        val_result = validate_fleet(account_logs)
        health     = store.health_score()

        # ── Optimize ──────────────────────────────────────────────────────────
        optim.update(
            ban_rate      = store.get_ema("ban_rate"),
            success_rate  = store.get_ema("success_rate"),
            anomaly_score = store.get_ema("anomaly_score"),
            health_score  = health,
            spike_flag    = val_result["spike_flag"],
        )

        # ── Cycle stats ───────────────────────────────────────────────────────
        stats = CycleStats(
            cycle             = cycle,
            accounts_active   = len(plans),
            accounts_skipped  = n_accounts - len(plans),
            role_distribution = role_counts,
            ban_count         = ban_count,
            success_count     = success_count,
            timing_entropy    = _timing_entropy(offsets),
            action_diversity  = _action_diversity(plans),
            collision_rate    = _collision_rate(offsets),
            health_score      = health,
            avg_reward        = policy.avg_reward(),
        )
        report.cycles.append(stats)
        LOGGER.info(
            "sim_cycle %d/%d active=%d bans=%d successes=%d health=%.2f",
            cycle + 1, n_cycles, len(plans), ban_count, success_count, health,
        )

    # ── Final report ──────────────────────────────────────────────────────────
    report.final_metrics  = store.snapshot()
    report.final_q_snap   = policy.snapshot()
    report.optimizer_snap = optim.snapshot()

    # Write JSON
    try:
        Path(output_path).write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
        LOGGER.info("sim_report_written path=%s", output_path)
    except Exception as exc:
        LOGGER.warning("sim_report_write_error %s", exc)

    return report
