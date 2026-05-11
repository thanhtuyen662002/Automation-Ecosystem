"""
core/distribution_engine.py — Distribution Engine

Maximises reach + profit while minimising ban risk.

Decides HOW content is distributed (ads / repost / organic), WHICH accounts
are used, HOW MUCH budget is spent, and estimates the BAN RISK of each plan.

Design contracts:
  - Deterministic given the same inputs + seed
  - No external API calls
  - Anti-ban rules enforced as hard limits (not suggestions)
  - Fail-safe: always returns a valid plan

Public API:
    plan_distribution(content, accounts, budget, signals, seed) -> dict
    assess_risk(plan)                                            -> float

SQLite table: distribution_history
    content_id   TEXT
    account_id   TEXT
    distributed_at REAL
    PRIMARY KEY (content_id, account_id)
"""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "distribution_engine.db"

# Distribution method thresholds
_ADS_CVR_MIN        = 0.04    # CVR gate for ads eligibility
_ADS_EPV_MIN        = 0.01    # EPV gate for ads eligibility
_ADS_ROI_PERCENTILE = 0.80    # only top 20% ROI content gets ads
_REPOST_CTR_MIN     = 0.03    # CTR gate for multi-account repost

# Anti-ban hard limits
_MAX_REPOSTS        = 3       # max accounts that can receive the same content
_MIN_REPOST_DELAY_S = 3600    # minimum seconds between reposts (1 h)

# Budget caps
_ADS_BUDGET_FRACTION  = 0.50  # max fraction of allocated budget on ads
_ADS_MAX_PER_CONTENT  = 50.0  # hard cap per content in $ for ads

# Risk scoring weights
_RISK_WEIGHTS = {
    "repost_count":    0.35,
    "account_health":  0.30,
    "delay_penalty":   0.20,
    "content_type":    0.15,
}

# Content-type risk priors (reup = low-risk; generate = higher novelty/risk)
_CONTENT_TYPE_RISK = {
    "reup":     0.10,
    "remark":   0.25,
    "generate": 0.40,
}

# ── Connection cache ──────────────────────────────────────────────────────────

_CONN_CACHE: dict[str, sqlite3.Connection] = {}


def _db_path() -> str:
    env = os.environ.get("DISTRIBUTION_ENGINE_DB")
    return env if env else str(_DEFAULT_DB)


def _get_conn() -> sqlite3.Connection:
    key = _db_path()
    if key in _CONN_CACHE:
        return _CONN_CACHE[key]
    if key != ":memory:":
        Path(key).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(key, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS distribution_history (
            content_id     TEXT NOT NULL,
            account_id     TEXT NOT NULL,
            distributed_at REAL NOT NULL,
            PRIMARY KEY (content_id, account_id)
        );
    """)
    conn.commit()
    _CONN_CACHE[key] = conn
    return conn


# ── History helpers ───────────────────────────────────────────────────────────

def _repost_count(content_id: str) -> int:
    """How many accounts have already received this content."""
    try:
        row = _get_conn().execute(
            "SELECT COUNT(*) AS cnt FROM distribution_history WHERE content_id = ?",
            (content_id,)
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


def _last_repost_ts(content_id: str, account_id: str) -> float:
    """Timestamp of last distribution of this content to this account (0 if never)."""
    try:
        row = _get_conn().execute(
            "SELECT distributed_at FROM distribution_history "
            "WHERE content_id = ? AND account_id = ?",
            (content_id, account_id)
        ).fetchone()
        return float(row["distributed_at"]) if row else 0.0
    except Exception:
        return 0.0


def _accounts_used(content_id: str) -> list[str]:
    """Return accounts that already received this content."""
    try:
        rows = _get_conn().execute(
            "SELECT account_id FROM distribution_history WHERE content_id = ?",
            (content_id,)
        ).fetchall()
        return [r["account_id"] for r in rows]
    except Exception:
        return []


def _record_distribution(content_id: str, account_id: str) -> None:
    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                """INSERT INTO distribution_history (content_id, account_id, distributed_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(content_id, account_id) DO UPDATE SET
                       distributed_at = excluded.distributed_at""",
                (content_id, account_id, time.time())
            )
    except Exception:
        pass


# ── Anti-ban: hook variation (delegates to Semantic Variation Engine) ──────────

def _derive_hook_variants_semantic(
    base_hook:    str,
    niche:        str,
    intent:       str,
    account_ids:  list[str],
    seed:         str,
) -> dict[str, dict]:
    """
    Generate per-account semantically distinct hook variants.
    Returns {account_id: {hook, angle_type, trigger, score}}.
    Gracefully falls back to base_hook on import failure.
    """
    try:
        from core.semantic_variation_engine import assign_variants_to_accounts
        # Build minimal account dicts for the engine
        acct_dicts = [{"account_id": aid} for aid in account_ids]
        assignments = assign_variants_to_accounts(
            base_hook=base_hook,
            niche=niche,
            accounts=acct_dicts,
            intent=intent,
            seed=seed,
        )
        return {
            a["account_id"]: {
                "hook":       a["hook"],
                "angle_type": a["angle_type"],
                "trigger":    a["trigger"],
                "score":      a["score"],
            }
            for a in assignments
        }
    except Exception:
        # Safe fallback: return base_hook for all accounts
        return {aid: {"hook": base_hook, "angle_type": "original",
                      "trigger": "", "score": 0.0}
                for aid in account_ids}


# ── Risk scorer ───────────────────────────────────────────────────────────────

def assess_risk(
    plan: dict[str, Any],
    accounts_detail: list[dict[str, Any]] | None = None,
) -> float:
    """
    Returns a risk score [0, 1] for a distribution plan.
    Higher = riskier.

    Factors:
        repost_count   — more reposts → higher risk
        account_health — average health of target accounts
        delay_penalty  — posting too close together
        content_type   — reup < remark < generate
    """
    n_accounts   = len(plan.get("accounts_used", []))
    content_type = str(plan.get("content_type", "reup")).lower()
    delay_s      = float(plan.get("min_delay_s", _MIN_REPOST_DELAY_S))

    # Repost count risk: 0 reposts = 0, 3 = max (1.0 normalised)
    repost_risk = min(1.0, n_accounts / _MAX_REPOSTS)

    # Account health: average health of used accounts; low health = higher risk
    if accounts_detail:
        healths = []
        for aid in plan.get("accounts_used", []):
            for a in accounts_detail:
                if str(a.get("account_id", "")) == aid:
                    healths.append(float(a.get("account_health", 0.5)))
                    break
        avg_health = sum(healths) / len(healths) if healths else 0.5
    else:
        avg_health = 0.5
    health_risk = 1.0 - avg_health   # low health = high risk

    # Delay penalty: below min → risk spike
    delay_risk = 0.0 if delay_s >= _MIN_REPOST_DELAY_S else (
        1.0 - min(1.0, delay_s / _MIN_REPOST_DELAY_S)
    )

    # Content type prior
    type_risk = _CONTENT_TYPE_RISK.get(content_type, 0.25)

    score = (
        _RISK_WEIGHTS["repost_count"]   * repost_risk  +
        _RISK_WEIGHTS["account_health"] * health_risk  +
        _RISK_WEIGHTS["delay_penalty"]  * delay_risk   +
        _RISK_WEIGHTS["content_type"]   * type_risk
    )
    return round(max(0.0, min(1.0, score)), 4)


# ── Distribution method selector ─────────────────────────────────────────────

def _select_method(
    unified_score: float,
    signals:       dict[str, Any],
) -> str:
    """
    Returns "ads" | "repost" | "organic" based solely on unified_score.
    Decision authority belongs to execution_brain via unified_score.

    Thresholds (mirror unified_scoring module):
        > 0.75  → ads      (top performers)
        > 0.50  → repost   (mid performers)
        ≤ 0.50  → organic  (explore / low signal)

    Anti-ban gates still enforced: EPV and CVR floor for ads.
    """
    epv = float(signals.get("epv", 0.0))
    cvr = float(signals.get("cvr", 0.0))

    if unified_score > 0.75:
        # Extra safety: require minimum CVR/EPV before paying for ads
        if cvr >= 0.04 and epv >= 0.01:
            return "ads"
        return "repost"   # good score but not safe for ads yet
    if unified_score > 0.50:
        return "repost"
    return "organic"



# ── Account selection ─────────────────────────────────────────────────────────

def _select_accounts(
    method:       str,
    strategy:     str,
    content_id:   str,
    accounts:     list[dict[str, Any]],
    seed:         str,
) -> list[str]:
    """
    Pick accounts for distribution respecting anti-ban rules.

    Rules:
      - Never reuse an account that already received this content
      - Max _MAX_REPOSTS accounts for repost; 1 for ads/organic
      - Prefer highest-health accounts
      - Deterministic selection via hash seed
    """
    already_used = set(_accounts_used(content_id))

    # Filter: exclude used, exclude unhealthy (< 0.3 health)
    eligible = [
        a for a in accounts
        if str(a.get("account_id", "")) not in already_used
        and float(a.get("account_health", 0.5)) >= 0.3
    ]
    if not eligible:
        return []

    # Sort by health desc, then deterministic tie-break
    def _sort_key(a: dict) -> tuple:
        h   = float(a.get("account_health", 0.5))
        aid = str(a.get("account_id", ""))
        tiebreak = int(hashlib.sha256((seed + aid).encode()).hexdigest()[:4], 16)
        return (-h, tiebreak)

    eligible.sort(key=_sort_key)

    if method == "repost":
        # Remaining repost slots = max_reposts - already_used
        remaining = max(0, _MAX_REPOSTS - len(already_used))
        return [str(a["account_id"]) for a in eligible[:remaining]]
    else:
        # ads / organic: single primary account
        return [str(eligible[0]["account_id"])] if eligible else []


# ── Budget calculation ────────────────────────────────────────────────────────

def _calculate_budget(
    method:            str,
    allocated_budget:  float,
    n_accounts:        int,
) -> float:
    """
    Returns actual budget consumed for this distribution event.

    ADS:     capped at min(50% of allocated, _ADS_MAX_PER_CONTENT)
    REPOST:  near-zero (operational cost only, modelled as $0.10/account)
    ORGANIC: $0
    """
    if method == "ads":
        cap = min(allocated_budget * _ADS_BUDGET_FRACTION, _ADS_MAX_PER_CONTENT)
        return round(max(0.0, cap), 4)
    if method == "repost":
        return round(0.10 * max(1, n_accounts), 4)
    return 0.0


# ── Main entry point ──────────────────────────────────────────────────────────

def plan_distribution(
    content:   dict[str, Any],
    accounts:  list[dict[str, Any]],
    budget:    float,
    signals:      dict[str, Any] | None = None,
    seed:         str = "",
    commit:       bool = False,
    unified_score: float = -1.0,   # -1 = not set; falls back to revenue_score
    roi_rank_pct:  float = 0.5,    # legacy param kept for backward compat
) -> dict[str, Any]:
    """
    Build a complete distribution plan for a single content item.

    Args:
        content:       dict with content_id, content_type, revenue_score,
                       unified_score, niche, intent_type, hook_text
        accounts:      list of account dicts with account_id, account_health
        budget:        budget allocated to this content (from budget_allocator)
        signals:       performance signals dict (ctr, cvr, epv)
        seed:          deterministic seed suffix
        commit:        if True, records distribution into SQLite history
        unified_score: authoritative score from execution_brain [0,1]
    """
    signals       = signals or {}
    content_id    = str(content.get("content_id",    ""))
    content_type  = str(content.get("content_type",  "reup")).lower()
    base_hook     = str(content.get("hook_text",     ""))
    rev_score     = float(content.get("revenue_score",     0.0))
    perf_score    = float(content.get("performance_score", 0.0))

    # Resolve unified_score: explicit param > content field > revenue_score fallback
    if unified_score < 0:
        unified_score = float(content.get("unified_score",
                              content.get("revenue_score", 0.5)))

    _seed = seed or f"{content_id}:{unified_score:.3f}:{int(time.time() // 3600)}"

    try:
        # ── 1. Choose distribution method (unified_score authority) ──────────────
        method = _select_method(unified_score, signals)

        # ── 2. Anti-ban: check existing repost count ──────────────────────────
        existing_reposts = _repost_count(content_id)
        if method == "repost" and existing_reposts >= _MAX_REPOSTS:
            method = "organic"   # hard cap reached → fall back to organic

        # ── 3. Select accounts ────────────────────────────────────────────────
        selected_accounts = _select_accounts(
            method, "passthrough", content_id, accounts, _seed
        )

        if not selected_accounts:
            # No eligible accounts → safe default
            method = "organic"
            selected_accounts = []

        # ── 4. Hook variants — Semantic Variation Engine ──────────────────────
        niche  = str(content.get("niche",       ""))
        intent = str(content.get("intent_type", ""))
        hook_variants: dict[str, Any] = {}
        if base_hook and selected_accounts:
            hook_variants = _derive_hook_variants_semantic(
                base_hook   = base_hook,
                niche       = niche or "general",
                intent      = intent,
                account_ids = selected_accounts,
                seed        = _seed,
            )

        # ── 5. Delay enforcement ──────────────────────────────────────────────
        # For repost: enforce _MIN_REPOST_DELAY_S between successive sends
        # (caller uses min_delay_s to schedule; we report it)
        delay_s = _MIN_REPOST_DELAY_S if method == "repost" else 0

        # ── 6. Budget ─────────────────────────────────────────────────────────
        budget_used = _calculate_budget(method, budget, len(selected_accounts))

        # ── 7. Build plan dict ────────────────────────────────────────────────
        plan: dict[str, Any] = {
            "content_id":        content_id,
            "distribution_type": method,
            "accounts_used":     selected_accounts,
            "hook_variants":     hook_variants,
            "budget_used":       budget_used,
            "content_type":      content_type,
            "min_delay_s":       delay_s,
            "risk_score":        0.0,   # filled below
            "reason":            "",
        }

        # ── 8. Risk assessment ────────────────────────────────────────────────
        plan["risk_score"] = assess_risk(plan, accounts)

        # ── 9. Reason string ──────────────────────────────────────────────────
        reason_parts = [
            f"unified_score={unified_score:.3f}",
            f"method={method}",
            f"rev={rev_score:.2f}",
            f"perf={perf_score:.2f}",
            f"accounts={len(selected_accounts)}",
            f"risk={plan['risk_score']:.3f}",
        ]
        if method == "repost":
            reason_parts.append(f"existing_reposts={existing_reposts}")
        plan["reason"] = " | ".join(reason_parts)

        # ── 10. Commit to history if requested ───────────────────────────────
        if commit:
            for aid in selected_accounts:
                _record_distribution(content_id, aid)

        return plan

    except Exception:
        return {
            "content_id":        content_id,
            "distribution_type": "organic",
            "accounts_used":     [],
            "hook_variants":     {},
            "budget_used":       0.0,
            "content_type":      content_type,
            "min_delay_s":       0,
            "risk_score":        0.5,
            "reason":            "fallback_safe_default",
        }


# ── Batch planner ─────────────────────────────────────────────────────────────

def plan_batch(
    contents:  list[dict[str, Any]],
    accounts:  list[dict[str, Any]],
    budgets:   dict[str, float],        # content_id -> allocated budget
    signals:   dict[str, dict[str, Any]] | None = None,  # content_id -> signals
    commit:    bool = False,
) -> list[dict[str, Any]]:
    """
    Plan distribution for a batch of contents.

    Computes roi_rank_pct internally from revenue_score ordering.
    Returns list of plans, sorted by budget_used DESC.
    """
    if not contents:
        return []

    # Rank by revenue_score to derive roi_rank_pct
    ranked = sorted(
        contents,
        key=lambda c: -float(c.get("revenue_score", 0.0))
    )
    n = len(ranked)

    plans: list[dict[str, Any]] = []
    for i, content in enumerate(ranked):
        cid          = str(content.get("content_id", ""))
        roi_pct      = i / max(n - 1, 1)    # 0.0 = best, 1.0 = worst
        content_sigs = (signals or {}).get(cid, {})
        budget       = float(budgets.get(cid, 0.0))
        seed         = f"batch:{cid}:{i}"

        plan = plan_distribution(
            content=content,
            accounts=accounts,
            budget=budget,
            signals=content_sigs,
            seed=seed,
            commit=commit,
            roi_rank_pct=roi_pct,
        )
        plans.append(plan)

    plans.sort(key=lambda p: -p["budget_used"])
    return plans
