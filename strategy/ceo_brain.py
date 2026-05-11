"""
strategy/ceo_brain.py — The CEO AI: strategic decision-maker above the execution brain.

Responsibilities:
  1. Goal Setting        — target views, revenue, risk tolerance, growth mode
  2. Dynamic Thresholds  — loosen/tighten execution gates based on performance
  3. Budget Allocation   — distribute post quota across niches via niche_score
  4. Risk Tolerance      — exploration rate varies per growth_mode
  5. System Overrides    — force publish, freeze account, boost niche
  6. Closed-loop Update  — update_from_metrics() feeds live data back in

The execution_brain calls get_strategy() to get a StrategyDirective
(threshold_modifier, exploration_rate, niche_budget) that modifies its gates
and exploration probability for that decision cycle.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("strategy.ceo_brain")

_DB_PATH = Path(os.environ.get("CEO_BRAIN_DB", "data/ceo_brain.db"))
_DDL = """
CREATE TABLE IF NOT EXISTS strategy_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS niche_performance (
    niche             TEXT NOT NULL,
    platform          TEXT NOT NULL,
    win_rate          REAL NOT NULL DEFAULT 0.0,
    avg_views         REAL NOT NULL DEFAULT 0.0,
    avg_revenue       REAL NOT NULL DEFAULT 0.0,
    posts_count       INTEGER NOT NULL DEFAULT 0,
    growth_potential  REAL NOT NULL DEFAULT 0.5,
    budget_share      REAL NOT NULL DEFAULT 0.0,
    updated_at        REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (niche, platform)
);

CREATE TABLE IF NOT EXISTS account_scores (
    account_id        TEXT NOT NULL,
    platform          TEXT NOT NULL,
    engagement_rate   REAL NOT NULL DEFAULT 0.0,
    conversion_rate   REAL NOT NULL DEFAULT 0.0,
    consistency       REAL NOT NULL DEFAULT 0.5,
    risk_penalty      REAL NOT NULL DEFAULT 0.0,
    account_score     REAL NOT NULL DEFAULT 0.5,
    status            TEXT NOT NULL DEFAULT 'active',
    allocation_share  REAL NOT NULL DEFAULT 0.0,
    updated_at        REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (account_id, platform)
);

CREATE TABLE IF NOT EXISTS strategy_overrides (
    target_id   TEXT NOT NULL,
    target_type TEXT NOT NULL,  -- 'account' | 'niche' | 'content'
    override    TEXT NOT NULL,  -- 'freeze' | 'boost' | 'kill' | 'force_publish'
    reason      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT 0.0,
    expires_at  REAL NOT NULL DEFAULT 0.0,
    active      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (target_id, override)
);

CREATE TABLE IF NOT EXISTS strategy_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    data       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT 0.0
);
"""

# ── Domain Types ──────────────────────────────────────────────────────────────

@dataclass
class StrategyState:
    # Goals
    target_daily_views:   float = 50_000.0
    target_daily_revenue: float = 50.0
    max_risk_level:       float = 0.5
    growth_mode:          str   = "balanced"   # conservative | balanced | aggressive | recovery

    # Dynamic runtime params (written by CEO, read by execution_brain)
    threshold_modifier:   float = 1.0
    exploration_rate:     float = 0.10

    # Performance snapshot (updated by update_from_metrics)
    actual_daily_views:   float = 0.0
    actual_daily_revenue: float = 0.0
    performance_ratio:    float = 1.0
    last_updated:         float = 0.0

    # Silent-failure tracking (Part 1.5)
    consecutive_low_cycles: int = 0   # cycles where performance_ratio < 0.6


@dataclass
class StrategyDirective:
    """Consumed by execution_brain.decide() for each decision cycle."""
    threshold_modifier:    float = 1.0
    exploration_rate:      float = 0.10
    niche_budget:          dict[str, float] = field(default_factory=dict)
    account_overrides:     dict[str, str]   = field(default_factory=dict)
    niche_overrides:       dict[str, str]   = field(default_factory=dict)
    growth_mode:           str              = "balanced"
    # Part 2 extensions
    account_limits:        dict[str, int]   = field(default_factory=dict)
    spawn_signals:         list[dict]       = field(default_factory=list)
    kill_signals:          list[dict]       = field(default_factory=list)
    diversity_boost:       float            = 0.0
    # Bug #2 + Part 2.5
    budget_diversity_factor: float          = 0.5
    platform_capital:      dict[str, float] = field(default_factory=dict)
    # Part 6: CEO strategy extension
    exploration_pressure:   float           = 0.0    # extra explore when locked in local optimum
    dominance_decay_active: bool            = False   # True when anti-dominance decay fires
    validation_strictness:  float           = 0.55    # mirrors _VALIDATION_THRESHOLD
    angle_performance:      dict[str, float] = field(default_factory=dict)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_DDL)
    con.commit()
    return con


def _softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    mx = max(scores)
    exps = [math.exp(s - mx) for s in scores]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


# ── CEO Brain ─────────────────────────────────────────────────────────────────

class CeoBrain:
    """
    Singleton-safe CEO AI. Use the module-level helper functions below
    rather than instantiating directly.
    """

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> StrategyState:
        con = _db()
        try:
            rows = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM strategy_state")}
        finally:
            con.close()

        state = StrategyState()
        for fname, ftype in StrategyState.__dataclass_fields__.items():
            if fname in rows:
                try:
                    raw = rows[fname]
                    hint = ftype.type
                    if hint in ("float", float):
                        setattr(state, fname, float(raw))
                    elif hint in ("int", int):
                        setattr(state, fname, int(raw))
                    elif hint in ("str", str):
                        setattr(state, fname, str(raw))
                except Exception:
                    pass
        return state

    def _save_state(self, state: StrategyState) -> None:
        con = _db()
        try:
            for fname in StrategyState.__dataclass_fields__:
                con.execute(
                    "INSERT OR REPLACE INTO strategy_state (key, value) VALUES (?, ?)",
                    (fname, str(getattr(state, fname))),
                )
            con.commit()
        finally:
            con.close()

    def _log(self, event: str, data: dict[str, Any]) -> None:
        con = _db()
        try:
            con.execute(
                "INSERT INTO strategy_log (event, data, created_at) VALUES (?, ?, ?)",
                (event, json.dumps(data), time.time()),
            )
            con.commit()
        finally:
            con.close()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_state(self) -> StrategyState:
        return self._load_state()

    def update_state(self, **kwargs: Any) -> StrategyState:
        state = self._load_state()
        changed = {}
        for k, v in kwargs.items():
            if hasattr(state, k):
                setattr(state, k, v)
                changed[k] = v
        self._apply_growth_mode(state)
        self._save_state(state)
        self._log("state_updated", changed)
        return state

    def _apply_growth_mode(self, state: StrategyState) -> None:
        """Sync exploration_rate and threshold_modifier to growth_mode."""
        if state.growth_mode == "aggressive":
            state.exploration_rate   = max(state.exploration_rate, 0.20)
            state.threshold_modifier = max(0.80, state.threshold_modifier)
        elif state.growth_mode == "conservative":
            state.exploration_rate   = min(state.exploration_rate, 0.05)
            state.threshold_modifier = min(1.20, state.threshold_modifier)
        elif state.growth_mode == "recovery":
            state.exploration_rate   = 0.25
            state.threshold_modifier = 0.85
        elif state.growth_mode == "domination":
            # Part 4.8: domination — low exploration, aggressive scaling
            state.exploration_rate   = max(0.05, min(state.exploration_rate, 0.08))
            state.threshold_modifier = min(1.15, max(1.05, state.threshold_modifier))

    def get_directive(self) -> StrategyDirective:
        """Main method called by execution_brain — returns a StrategyDirective."""
        state    = self._load_state()
        budget   = self._compute_niche_budget()
        overrides = self._active_overrides()

        # ── Account limits (Part 2: portfolio tiers) ────────────────────────────
        acct_limits: dict[str, int] = {}
        for a in self.get_account_scores():
            score = a["account_score"]
            daily_max = {"tiktok": 5, "facebook": 4}.get(a.get("platform", "tiktok"), 5)
            if score < 0.25:
                acct_limits[a["account_id"]] = 0    # frozen
            elif score < 0.50:
                acct_limits[a["account_id"]] = max(1, round(daily_max * 0.4))
            elif score < 0.70:
                acct_limits[a["account_id"]] = round(daily_max * 0.8)
            else:  # > 0.70 → scale
                acct_limits[a["account_id"]] = round(daily_max * 1.5)

        # ── Spawn signals (Part 2: spawn/kill system) ──────────────────────────
        spawn_signals = self._compute_spawn_signals()
        kill_signals  = self._compute_kill_signals()

        # Part 2.1: Exploration pressure — system lock guard
        # When top-3 niches capture >75% of budget → force exploration
        top3_share = sum(sorted(budget.values(), reverse=True)[:3]) if budget else 0.0
        exploration_pressure = 0.0
        if top3_share > 0.75:
            state = self._load_state()
            state.exploration_rate  = min(0.30, state.exploration_rate + 0.05)
            state.threshold_modifier = max(0.70, state.threshold_modifier - 0.05)
            self._save_state(state)
            exploration_pressure = 0.05

        # ── Diversity boost (echo-chamber guard) ───────────────────────────────
        diversity_boost = 0.0
        if budget:
            if top3_share > 0.70:
                diversity_boost = 0.05

        # Part 4.2: Niche strategy actions (dominate / expand / exit)
        niche_actions: dict[str, str] = {}
        for np in self.get_niche_performance():
            n = np["niche"]
            wr = float(np.get("win_rate", 0))
            gp = float(np.get("growth_potential", 0.5))
            sat = float(budget.get(n, 0))  # budget share as saturation proxy
            if wr > 0.65 and sat < 0.4:
                niche_actions[n] = "dominate"
            elif wr > 0.55 and gp > 0.7:
                niche_actions[n] = "expand"
            elif wr < 0.35:
                niche_actions[n] = "exit"
            else:
                niche_actions[n] = "normal"

        # Part 2.2: Novelty injection — inactive niches get temporary budget boost
        # Track cycle number via a lightweight counter on the instance
        _cycle = getattr(self, "_cycle_count", 0) + 1
        self._cycle_count = _cycle
        _niche_last_active: dict[str, int] = getattr(self, "_niche_last_active", {})
        for n, action in niche_actions.items():
            if action in ("exit", "normal"):
                last = _niche_last_active.get(n, _cycle)
                if (_cycle - last) > 5 and n in budget:
                    budget[n] = round(min(0.60, budget[n] * 1.2), 4)
            else:
                _niche_last_active[n] = _cycle   # mark as active
        self._niche_last_active = _niche_last_active
        # Re-normalise after novelty injection
        _bt = sum(budget.values()) or 1.0
        budget = {n: round(v / _bt, 4) for n, v in budget.items()}

        # Part 2.3: Anti-dominance decay
        # If a niche holds >60% for 3+ cycles → decay by 0.9/cycle
        _dominance_history: dict[str, int] = getattr(self, "_dominance_history", {})
        dominance_decay_active = False
        for n, share in budget.items():
            if share > 0.60:
                _dominance_history[n] = _dominance_history.get(n, 0) + 1
                if _dominance_history[n] >= 3:
                    budget[n] = round(budget[n] * 0.90, 4)
                    dominance_decay_active = True
            else:
                _dominance_history[n] = 0
        self._dominance_history = _dominance_history
        # Re-normalise after decay
        _bt2 = sum(budget.values()) or 1.0
        budget = {n: round(v / _bt2, 4) for n, v in budget.items()}

        # Merge computed actions into niche_overrides (manual overrides win)
        merged_niche_overrides = dict(niche_actions)
        merged_niche_overrides.update(overrides["niches"])  # manual wins

        # Part 2.5: Platform capital layer (performance-weighted per platform)
        platform_scores: dict[str, list[float]] = {}
        for a in self.get_account_scores():
            p = a.get("platform", "tiktok")
            s = float(a.get("account_score", 0))
            platform_scores.setdefault(p, []).append(s)
        plat_capital: dict[str, float] = {
            p: round(sum(v) / len(v), 4)
            for p, v in platform_scores.items() if v
        }
        # Normalise to [0,1]
        _pmax = max(plat_capital.values(), default=1.0) or 1.0
        plat_capital = {p: round(v / _pmax, 4) for p, v in plat_capital.items()}

        # Budget diversity factor from budget computation
        _div_f = getattr(self, "_last_diversity_factor", 0.5)

        # Part 4: CEO Integration - Angle Performance
        angle_performance: dict[str, float] = {}
        try:
            from core.angle_engine import get_best_angles
            for n in budget.keys():
                best_all = get_best_angles(n, n=5)
                best = best_all[0] if best_all else None
                dom = best.dominance_score if best else 0.0
                angle_performance[n] = dom
                
                # Part 4 (Capital Shift): 2+ high amplification angles
                high_amp_count = sum(1 for a in best_all if getattr(a, "amplification_score", 0.0) > 0.6)
                if high_amp_count >= 2:
                    budget[n] = round(budget[n] * 1.15, 4)
                    state.exploration_rate = max(0.05, state.exploration_rate - 0.02)
                
                # If top angles in niche > threshold: boost niche_budget
                if dom > 0.7:
                    budget[n] = round(budget[n] * 1.20, 4)
                    
                # If no strong angles in niche: increase exploration_rate
                if dom < 0.3:
                    state.exploration_rate = min(0.30, state.exploration_rate + 0.02)
                    
            # Re-normalize budget
            _b_tot = sum(budget.values()) or 1.0
            budget = {k: round(v / _b_tot, 4) for k, v in budget.items()}
        except Exception as exc:
            LOGGER.warning("angle_integration_error %s", exc)

        # Part 7: CEO Pre-Trend Feedback Loop
        # If a niche has 2+ angles with early_trend_score > 0.55, increase
        # exploration globally (+0.02) and redistribute capital (+10%) toward it.
        try:
            from core.angle_engine import get_best_angles, _PRE_TREND_THRESHOLD
            _pre_trend_niches: list[str] = []
            for n in list(budget.keys()):
                best_all_ets = get_best_angles(n, n=10)
                pre_trend_count = sum(
                    1 for a in best_all_ets
                    if getattr(a, "early_trend_score", 0.0) > _PRE_TREND_THRESHOLD
                )
                if pre_trend_count >= 2:
                    _pre_trend_niches.append(n)
                    budget[n] = round(budget[n] * 1.10, 4)

            if _pre_trend_niches:
                state.exploration_rate = min(0.30, state.exploration_rate + 0.02)
                # Re-normalize budget after capital injection
                _bpt = sum(budget.values()) or 1.0
                budget = {k: round(v / _bpt, 4) for k, v in budget.items()}
                LOGGER.info("pre_trend_ceo_boost niches=%s", _pre_trend_niches)
        except Exception as _exc:
            LOGGER.debug("pre_trend_ceo_error %s", _exc)

        # Part 8: CEO Pattern Cluster Feedback Loop
        # Scan winner memory for clusters that repeatedly produce winners.
        # If a cluster success rate > threshold, raise exploration toward it.
        try:
            from core.angle_engine import get_winner_memory
            _winner_mem = get_winner_memory()
            if _winner_mem:
                # Group by (emotion_type, content_format) as cluster key
                _cluster_counts: dict[str, int] = {}
                for _entry in _winner_mem.values():
                    _sig = _entry.get("signature", {})
                    _cluster_key = f"{_sig.get('emotion_type','')}:{_sig.get('content_format','')}"
                    _cluster_counts[_cluster_key] = _cluster_counts.get(_cluster_key, 0) + 1

                # Cluster with >= 3 winners → nudge exploration +0.03 (NOT global reduction)
                _hot_clusters = [k for k, cnt in _cluster_counts.items() if cnt >= 3]
                if _hot_clusters:
                    state.exploration_rate = min(0.30, state.exploration_rate + 0.03)
                    LOGGER.info("pattern_cluster_boost clusters=%s", _hot_clusters)
        except Exception as _exc:
            LOGGER.debug("pattern_cluster_ceo_error %s", _exc)

        return StrategyDirective(
            threshold_modifier      = state.threshold_modifier,
            exploration_rate        = state.exploration_rate,
            niche_budget            = budget,
            account_overrides       = overrides["accounts"],
            niche_overrides         = merged_niche_overrides,
            growth_mode             = state.growth_mode,
            account_limits          = acct_limits,
            spawn_signals           = spawn_signals,
            kill_signals            = kill_signals,
            diversity_boost         = diversity_boost,
            budget_diversity_factor = _div_f,
            platform_capital        = plat_capital,
            exploration_pressure    = exploration_pressure,
            dominance_decay_active  = dominance_decay_active,
            validation_strictness   = 0.55,
            angle_performance       = angle_performance,
        )

    # ── Niche Budget Allocation ───────────────────────────────────────────────

    def _compute_niche_budget(self) -> dict[str, float]:
        """
        Capital allocation (Part 4.3):
          score = 0.4*profit + 0.3*growth + 0.2*stability + 0.1*dominance_potential
        Anti-monopoly: cap at 60%, 0.95 decay blend.
        """
        con = _db()
        try:
            rows = con.execute(
                "SELECT niche, win_rate, avg_views, avg_revenue, growth_potential, budget_share"
                " FROM niche_performance"
            ).fetchall()
        finally:
            con.close()

        if not rows:
            return {}

        max_views   = max((r["avg_views"]   for r in rows), default=1.0) or 1.0
        max_revenue = max((r["avg_revenue"] for r in rows), default=1.0) or 1.0
        niches = [r["niche"] for r in rows]
        scores = [
            0.40 * (r["win_rate"] * (r["avg_revenue"] / max_revenue))   # profit
            + 0.30 * r["growth_potential"]                               # growth
            + 0.20 * (r["avg_views"] / max_views)                       # stability
            + 0.10 * min(1.0, r["win_rate"] * r["growth_potential"])    # dominance_potential
            for r in rows
        ]
        shares = _softmax(scores)

        prev_shares = {r["niche"]: r["budget_share"] for r in rows}
        decayed = [round(0.7 * s + 0.3 * prev_shares.get(n, s) * 0.95, 4)
                   for n, s in zip(niches, shares)]
        total  = sum(decayed) or 1.0
        shares = [d / total for d in decayed]

        # Anti-monopoly: cap at 60%, redistribute excess
        capped       = [min(s, 0.60) for s in shares]
        over         = sum(s - c for s, c in zip(shares, capped))
        remain       = [1.0 if c < 0.60 else 0.0 for c in capped]
        total_remain = sum(remain) or 1.0
        final_shares = [round(c + over * (r / total_remain), 4)
                        for c, r in zip(capped, remain)]
        raw_budget = dict(zip(niches, final_shares))

        # Bug #2 FIX: Entropy regularization — prevent capital concentration
        import math as _math
        _vals = [v for v in raw_budget.values() if v > 0]
        if len(_vals) > 1:
            entropy     = -sum(p * _math.log(p) for p in _vals if p > 0)
            max_entropy = _math.log(len(_vals))
            diversity_f = round(entropy / max_entropy, 4) if max_entropy > 0 else 1.0
        else:
            entropy     = 0.0
            diversity_f = 0.0

        # Adjust: concentrated allocation → forced redistribution
        adjusted = {
            n: round(v * (0.7 + 0.3 * diversity_f), 4)
            for n, v in raw_budget.items()
        }
        # Re-normalise to sum=1
        _adj_total = sum(adjusted.values()) or 1.0
        final_budget = {n: round(v / _adj_total, 4) for n, v in adjusted.items()}
        # Store entropy metadata for caller (returned via _last_budget_meta)
        self._last_entropy          = entropy
        self._last_diversity_factor = diversity_f
        return final_budget

    def update_niche_performance(
        self, niche: str, platform: str,
        win_rate: float, avg_views: float, avg_revenue: float,
        posts_count: int, growth_potential: float = 0.5,
    ) -> None:
        con = _db()
        try:
            # Compute budget_score locally before persisting
            budget_score = win_rate * growth_potential
            con.execute(
                "INSERT OR REPLACE INTO niche_performance"
                " (niche, platform, win_rate, avg_views, avg_revenue,"
                "  posts_count, growth_potential, budget_share, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (niche, platform, win_rate, avg_views, avg_revenue,
                 posts_count, growth_potential, budget_score, time.time()),
            )
            con.commit()
        finally:
            con.close()

    # ── Account Scoring & Capital Allocation ──────────────────────────────────

    def score_account(
        self,
        account_id:      str,
        platform:        str,
        engagement_rate: float,
        conversion_rate: float,
        consistency:     float = 0.5,
        risk_penalty:    float = 0.0,
        growth_rate:     float = 0.0,
    ) -> float:
        """
        account_score = engagement*0.4 + conversion*0.3 + consistency*0.2 + growth*0.1
        4 tiers: <0.25=freeze | 0.25-0.5=limit | 0.5-0.7=normal | >0.7=scale
        """
        score = (
            engagement_rate * 0.4
            + conversion_rate  * 0.3
            + consistency      * 0.2
            + min(1.0, max(0.0, growth_rate)) * 0.1
        )
        score = round(max(0.0, min(1.0, score)), 4)

        # 4-tier status classification (Part 2)
        if score < 0.25:
            status = "underperforming"   # freeze
        elif score < 0.50:
            status = "limited"           # limit posts
        elif score < 0.70:
            status = "active"            # normal
        else:
            status = "winning"           # scale

        con = _db()
        try:
            con.execute(
                "INSERT OR REPLACE INTO account_scores"
                " (account_id, platform, engagement_rate, conversion_rate,"
                "  consistency, risk_penalty, account_score, status, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, platform, engagement_rate, conversion_rate,
                 consistency, risk_penalty, score, status, time.time()),
            )
            con.commit()
        finally:
            con.close()

        # Auto-actions
        if score < 0.25:
            self.add_override(account_id, "account", "freeze",
                              reason=f"auto:score<0.25({score:.2f})", ttl_hours=24.0)
            self._log("account_auto_frozen", {"account_id": account_id, "score": score})
        elif score > 0.65:
            # Boost winning accounts — allocate more posts
            self.add_override(account_id, "account", "boost",
                              reason=f"auto:score>0.65({score:.2f})", ttl_hours=12.0)
            self._log("account_auto_boosted", {"account_id": account_id, "score": score})

        return score

    def allocate_posts(self, account_ids: list[str], platform: str, total_posts: int) -> dict[str, int]:
        """
        allocation = softmax(account_scores)
        posts_per_account = int(allocation * total_posts)
        """
        if not account_ids:
            return {}

        con = _db()
        try:
            rows = {
                r["account_id"]: r["account_score"]
                for r in con.execute(
                    "SELECT account_id, account_score FROM account_scores WHERE platform=?",
                    (platform,),
                ).fetchall()
            }
        finally:
            con.close()

        scores = [rows.get(a, 0.5) for a in account_ids]
        shares = _softmax(scores)
        result = {a: max(1, round(s * total_posts)) for a, s in zip(account_ids, shares)}

        # Update allocation_share
        con = _db()
        try:
            for a, s in zip(account_ids, shares):
                con.execute(
                    "UPDATE account_scores SET allocation_share=? WHERE account_id=? AND platform=?",
                    (round(s, 4), a, platform),
                )
            con.commit()
        finally:
            con.close()
        return result

    def get_account_scores(self) -> list[dict[str, Any]]:
        con = _db()
        try:
            rows = con.execute(
                "SELECT * FROM account_scores ORDER BY account_score DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    def get_niche_performance(self) -> list[dict[str, Any]]:
        con = _db()
        try:
            rows = con.execute(
                "SELECT * FROM niche_performance ORDER BY win_rate DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    # ── Spawn / Kill system (Part 2) ──────────────────────────────────────────

    def _compute_spawn_signals(self) -> list[dict[str, Any]]:
        """Recommend spawning new accounts when niche ROI > 1.5 and utilization > 80%."""
        signals: list[dict[str, Any]] = []
        try:
            niches = self.get_niche_performance()
            for n in niches:
                roi = n.get("win_rate", 0) * (n.get("avg_revenue", 0) / max(1.0, n.get("avg_views", 1)))
                util = n.get("budget_share", 0)  # high share = high utilization
                if roi > 1.5 or (n.get("win_rate", 0) > 0.7 and util > 0.40):
                    signals.append({
                        "niche":  n["niche"],
                        "reason": f"win_rate={n.get('win_rate',0):.2f} util={util:.2f}",
                    })
        except Exception as exc:
            LOGGER.warning("spawn_signals_error %s", exc)
        return signals

    def _compute_kill_signals(self) -> list[dict[str, Any]]:
        """Flag accounts with score < 0.20 for 3+ cycles (tracked via strategy_log)."""
        signals: list[dict[str, Any]] = []
        try:
            accts = self.get_account_scores()
            con   = _db()
            try:
                for a in accts:
                    if a["account_score"] < 0.20:
                        cycles = con.execute(
                            "SELECT COUNT(*) FROM strategy_log WHERE event='account_low_score'"
                            " AND json_extract(data,'$.account_id')=? AND created_at > ?",
                            (a["account_id"], time.time() - 7 * 86400),
                        ).fetchone()[0]
                        if cycles >= 3:
                            signals.append({
                                "account_id": a["account_id"],
                                "reason": f"score={a['account_score']:.2f} for {cycles} cycles",
                            })
            finally:
                con.close()
        except Exception as exc:
            LOGGER.warning("kill_signals_error %s", exc)
        return signals

    # ── Overrides ─────────────────────────────────────────────────────────────

    def add_override(
        self, target_id: str, target_type: str, override: str,
        reason: str = "", ttl_hours: float = 24.0,
    ) -> None:
        """target_type: 'account'|'niche'|'content'. override: 'freeze'|'boost'|'kill'|'force_publish'"""
        con = _db()
        try:
            con.execute(
                "INSERT OR REPLACE INTO strategy_overrides"
                " (target_id, target_type, override, reason, created_at, expires_at, active)"
                " VALUES (?, ?, ?, ?, ?, ?, 1)",
                (target_id, target_type, override, reason,
                 time.time(), time.time() + ttl_hours * 3600),
            )
            con.commit()
        finally:
            con.close()
        self._log("override_added", {"target_id": target_id, "override": override})

    def remove_override(self, target_id: str, override: str) -> None:
        con = _db()
        try:
            con.execute(
                "UPDATE strategy_overrides SET active=0 WHERE target_id=? AND override=?",
                (target_id, override),
            )
            con.commit()
        finally:
            con.close()

    def _active_overrides(self) -> dict[str, dict[str, str]]:
        """Returns {"accounts": {id→override}, "niches": {niche→override}}"""
        con = _db()
        try:
            now  = time.time()
            rows = con.execute(
                "SELECT target_id, target_type, override FROM strategy_overrides"
                " WHERE active=1 AND expires_at > ?",
                (now,),
            ).fetchall()
        finally:
            con.close()

        result: dict[str, dict[str, str]] = {"accounts": {}, "niches": {}, "content": {}}
        for r in rows:
            bucket = r["target_type"] + "s"
            if bucket in result:
                result[bucket][r["target_id"]] = r["override"]
        return result

    # ── Dynamic Threshold Control ─────────────────────────────────────────────

    def _recompute_threshold_modifier(self, state: StrategyState) -> tuple[float, float]:
        """
        4-branch CEO control loop (Part 4.8):
          ratio < 0.6  → recovery: modifier=0.80, explore=0.25
          ratio < 1.0  → balanced: modifier=1.00, explore=0.10
          ratio > 1.2  → domination: modifier=1.10, explore=0.06
          (recovery mode trumps all via _apply_growth_mode)
        Returns (threshold_modifier, exploration_rate).
        """
        ratio = state.performance_ratio
        if ratio < 0.5:
            return 0.80, 0.20
        if ratio > 1.3:
            return 1.10, 0.05
        return 1.00, 0.10

    # ── Closed-loop Update ────────────────────────────────────────────────────

    def update_from_metrics(
        self,
        actual_daily_views:   float,
        actual_daily_revenue: float,
        niche_data:           list[dict[str, Any]] | None = None,
        account_data:         list[dict[str, Any]] | None = None,
    ) -> StrategyState:
        """
        Part 4 — Closed loop: metrics → strategy → execution.
        Called by the orchestrator after each reporting cycle.
        """
        state = self._load_state()

        state.actual_daily_views   = actual_daily_views
        state.actual_daily_revenue = actual_daily_revenue

        # Performance ratio (views-weighted: 60% views, 40% revenue)
        view_ratio    = actual_daily_views   / max(1.0, state.target_daily_views)
        revenue_ratio = actual_daily_revenue / max(0.01, state.target_daily_revenue)
        state.performance_ratio = round(0.6 * view_ratio + 0.4 * revenue_ratio, 4)

        # Part 1.5 / 4.8: Automatic mode switching
        if state.performance_ratio < 0.6:
            state.consecutive_low_cycles += 1
        else:
            state.consecutive_low_cycles = 0

        # Part 4 + 2.7: multi_platform_win + entropy gate for domination
        _acct_scores = self.get_account_scores()
        _winning_platforms = {a["platform"] for a in _acct_scores
                              if float(a.get("account_score", 0)) > 0.65}
        _multi_platform_win = len(_winning_platforms) > 1
        _div_factor = getattr(self, "_last_diversity_factor", 0.0)
        _entropy_ok = _div_factor > 0.7   # diversity_factor proxies entropy/max_entropy

        if state.consecutive_low_cycles >= 5 and state.growth_mode != "recovery":
            state.growth_mode = "recovery"
            self._log("recovery_mode_triggered", {
                "consecutive_cycles": state.consecutive_low_cycles,
                "performance_ratio":  state.performance_ratio,
            })
            LOGGER.warning("RECOVERY MODE TRIGGERED after %d low cycles", state.consecutive_low_cycles)
        elif (state.performance_ratio > 1.2
              and state.consecutive_low_cycles == 0
              and _multi_platform_win
              and _entropy_ok
              and state.growth_mode not in ("recovery", "domination")):
            state.growth_mode = "domination"
            self._log("domination_mode_triggered", {
                "performance_ratio": state.performance_ratio,
                "winning_platforms": list(_winning_platforms),
                "diversity_factor":  _div_factor,
            })
            LOGGER.info("DOMINATION MODE ratio=%.2f platforms=%s entropy_ok=%s",
                        state.performance_ratio, list(_winning_platforms), _entropy_ok)
        elif state.performance_ratio <= 1.2 and state.growth_mode == "domination":
            state.growth_mode = "balanced"




        # Dynamically recompute threshold_modifier + exploration_rate (coupled)
        state.threshold_modifier, state.exploration_rate = \
            self._recompute_threshold_modifier(state)

        # Growth-mode hard overrides take precedence over ratio-based values
        self._apply_growth_mode(state)
        state.last_updated = time.time()
        self._save_state(state)

        # Update niche performance table
        if niche_data:
            for nd in niche_data:
                self.update_niche_performance(
                    niche           = nd.get("niche", ""),
                    platform        = nd.get("platform", "tiktok"),
                    win_rate        = float(nd.get("win_rate", 0.0)),
                    avg_views       = float(nd.get("avg_views", 0.0)),
                    avg_revenue     = float(nd.get("avg_revenue", 0.0)),
                    posts_count     = int(nd.get("posts_count", 0)),
                    growth_potential= float(nd.get("growth_potential", 0.5)),
                )

        # Update account scores + auto-actions
        if account_data:
            for ad in account_data:
                self.score_account(
                    account_id      = ad.get("account_id", ""),
                    platform        = ad.get("platform", "tiktok"),
                    engagement_rate = float(ad.get("engagement_rate", 0.0)),
                    conversion_rate = float(ad.get("conversion_rate", 0.0)),
                    consistency     = float(ad.get("consistency", 0.5)),
                    risk_penalty    = float(ad.get("risk_penalty", 0.0)),
                )

        # ── Expansion trigger: scale fleet if ROI is strong ──────────────────
        # avg_roi proxy: performance_ratio; utilization = posts vs capacity
        all_accts = self.get_account_scores()
        active    = [a for a in all_accts if a.get("status") not in ("frozen", "underperforming")]
        if len(active) > 0:
            avg_score = sum(a["account_score"] for a in active) / len(active)
            # utilization: accounts at >80% allocation
            high_util = [a for a in active if a.get("allocation_share", 0) > 0.8 / max(1, len(active))]
            utilization = len(high_util) / max(1, len(active))
            if (state.performance_ratio > 1.0
                    and avg_score > 0.65
                    and utilization > 0.80
                    and len(active) < 20):
                self._log("expansion_trigger", {
                    "avg_score": avg_score,
                    "utilization": utilization,
                    "active_accounts": len(active),
                    "action": "spawn_new_account",
                })

        # ── Kill switch: pause niche if ROI < 0.5 for 2+ cycles ─────────────
        if niche_data:
            con = _db()
            try:
                for nd in niche_data:
                    n   = nd.get("niche", "")
                    roi = float(nd.get("win_rate", 0.5))   # win_rate as ROI proxy
                    if roi < 0.5:
                        losses = con.execute(
                            "SELECT COUNT(*) FROM strategy_log WHERE event='niche_loss_cycle'"
                            " AND json_extract(data,'$.niche')=? AND created_at > ?",
                            (n, time.time() - 7 * 86400),
                        ).fetchone()[0]
                        con.execute(
                            "INSERT INTO strategy_log (event, data, created_at) VALUES (?,?,?)",
                            ("niche_loss_cycle", json.dumps({"niche": n, "roi": roi}), time.time()),
                        )
                        if losses >= 2:
                            # Inline the override to avoid opening a second connection
                            con.execute(
                                "INSERT OR REPLACE INTO strategy_overrides"
                                " (target_id, target_type, override, reason, created_at, expires_at, active)"
                                " VALUES (?, ?, ?, ?, ?, ?, 1)",
                                (n, "niche", "restrict",
                                 f"kill_switch:{losses+1}_loss_cycles",
                                 time.time(), time.time() + 48 * 3600),
                            )
                            con.execute(
                                "INSERT INTO strategy_log (event, data, created_at) VALUES (?,?,?)",
                                ("niche_paused", json.dumps({"niche": n, "cycles": losses + 1}), time.time()),
                            )
                            LOGGER.info("kill_switch niche=%s cycles=%d", n, losses + 1)
            finally:
                con.commit()
                con.close()


        self._log("metrics_updated", {
            "views": actual_daily_views,
            "revenue": actual_daily_revenue,
            "performance_ratio": state.performance_ratio,
            "threshold_modifier": state.threshold_modifier,
            "exploration_rate": state.exploration_rate,
        })
        return state

    # ── Recommendations ───────────────────────────────────────────────────────

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Human-readable strategic recommendations based on current state."""
        state = self._load_state()
        recs: list[dict[str, Any]] = []

        if state.performance_ratio < 0.5:
            recs.append({
                "priority": "critical",
                "action":   "lower_thresholds",
                "message":  f"Performance at {state.performance_ratio:.0%} of target — loosening gates to generate more reach.",
            })
        elif state.performance_ratio > 1.3:
            recs.append({
                "priority": "info",
                "action":   "tighten_thresholds",
                "message":  f"Exceeding target by {(state.performance_ratio-1):.0%} — tightening quality gate to improve ROI.",
            })

        # Niche domination
        con = _db()
        try:
            high_perf = con.execute(
                "SELECT niche, win_rate FROM niche_performance WHERE win_rate > 0.6 ORDER BY win_rate DESC LIMIT 3"
            ).fetchall()
            for r in high_perf:
                recs.append({
                    "priority": "high",
                    "action":   "boost_niche",
                    "niche":    r["niche"],
                    "message":  f"Niche '{r['niche']}' has {r['win_rate']:.0%} win rate — allocate more budget.",
                })

            # Underperforming accounts
            dead_accts = con.execute(
                "SELECT account_id, account_score FROM account_scores WHERE account_score < 0.25"
            ).fetchall()
            for r in dead_accts:
                recs.append({
                    "priority": "warning",
                    "action":   "review_account",
                    "account_id": r["account_id"],
                    "message":  f"Account {r['account_id']} score={r['account_score']:.2f} — consider pausing.",
                })
        finally:
            con.close()

        # New account trigger
        accts = self.get_account_scores()
        active = [a for a in accts if a["status"] != "frozen"]
        if len(active) > 0:
            avg_score = sum(a["account_score"] for a in active) / len(active)
            if avg_score > 0.65 and len(active) < 10:
                recs.append({
                    "priority": "high",
                    "action":   "create_new_account",
                    "message":  f"Fleet avg score={avg_score:.2f} and capacity available — create new accounts to scale.",
                })

        return recs

    # ── Strategy Log ─────────────────────────────────────────────────────────

    def get_log(self, limit: int = 50) -> list[dict[str, Any]]:
        con = _db()
        try:
            rows = con.execute(
                "SELECT event, data, created_at FROM strategy_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [{"event": r["event"], "data": json.loads(r["data"]), "ts": r["created_at"]} for r in rows]
        finally:
            con.close()


# ── Module-level singleton helpers ────────────────────────────────────────────

_ceo: CeoBrain | None = None


def _get_ceo() -> CeoBrain:
    global _ceo
    if _ceo is None:
        _ceo = CeoBrain()
    return _ceo


def get_strategy() -> StrategyDirective:
    """Called by execution_brain.decide() each cycle."""
    try:
        return _get_ceo().get_directive()
    except Exception as exc:
        LOGGER.warning("get_strategy_failed %s", exc)
        return StrategyDirective()   # neutral defaults


def get_state() -> StrategyState:
    return _get_ceo().get_state()


def update_state(**kwargs: Any) -> StrategyState:
    return _get_ceo().update_state(**kwargs)


def update_from_metrics(
    actual_daily_views: float,
    actual_daily_revenue: float,
    niche_data: list[dict[str, Any]] | None = None,
    account_data: list[dict[str, Any]] | None = None,
) -> StrategyState:
    return _get_ceo().update_from_metrics(
        actual_daily_views, actual_daily_revenue, niche_data, account_data
    )


def get_recommendations() -> list[dict[str, Any]]:
    return _get_ceo().get_recommendations()


def get_niche_performance() -> list[dict[str, Any]]:
    return _get_ceo().get_niche_performance()


def get_account_scores() -> list[dict[str, Any]]:
    return _get_ceo().get_account_scores()


def score_account(account_id: str, platform: str, **kwargs: float) -> float:
    return _get_ceo().score_account(account_id, platform, **kwargs)


def allocate_posts(account_ids: list[str], platform: str, total_posts: int) -> dict[str, int]:
    return _get_ceo().allocate_posts(account_ids, platform, total_posts)


def add_override(target_id: str, target_type: str, override: str,
                 reason: str = "", ttl_hours: float = 24.0) -> None:
    _get_ceo().add_override(target_id, target_type, override, reason, ttl_hours)


def remove_override(target_id: str, override: str) -> None:
    _get_ceo().remove_override(target_id, override)


def get_log(limit: int = 50) -> list[dict[str, Any]]:
    return _get_ceo().get_log(limit)


def update_niche_performance(
    niche: str, platform: str,
    win_rate: float, avg_views: float, avg_revenue: float,
    posts_count: int, growth_potential: float = 0.5,
) -> None:
    _get_ceo().update_niche_performance(
        niche=niche, platform=platform, win_rate=win_rate,
        avg_views=avg_views, avg_revenue=avg_revenue,
        posts_count=posts_count, growth_potential=growth_potential,
    )
