"""
core/portfolio_memory.py — Portfolio Memory  (v5)

v5 changes:
  Part A — True causal lift via accumulated count statistics (not single-event lift)
  Part B — Bayesian confidence: var = sr*(1-sr)/n → conf = 1 - sqrt(var)*3
  Part C — Early trend: trend = velocity + 0.5*acceleration
  Fix 1  — Boost cap raised to 1.25
  Fix 3  — Exploration safety floor: conf < 0.3 → rate >= 0.20
  New    — record_causal_exposure() accumulates count stats, replaces update_pattern_graph
  New    — update_pattern_performance(learning_weight) for scale feedback dampening
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB        = Path("data") / "portfolio_memory.db"
_ALPHA             = 0.20
_ALPHA_GRAPH       = 0.30
_FATIGUE_THRESHOLD = 0.70
_SAT_LAMBDA        = 0.1
_SAT_THRESHOLD     = 20.0
_SAT_MAX_PENALTY   = 0.25
_GRAPH_MIN_LIFT    = 1.05
_GRAPH_MIN_SAMPLES = 30    # minimum exposures before lift is trusted
_TS_WINDOW         = 10

_CONN: dict[str, sqlite3.Connection] = {}


def _db_path() -> str:
    return os.environ.get("PORTFOLIO_MEMORY_DB", str(_DEFAULT_DB))


def _get_conn() -> sqlite3.Connection:
    key = _db_path()
    if key in _CONN:
        return _CONN[key]
    if key != ":memory:":
        Path(key).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(key, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS niche_memory (
            niche TEXT PRIMARY KEY,
            avg_revenue_score REAL DEFAULT 0.5,
            avg_ctr  REAL DEFAULT 0.0,
            avg_cvr  REAL DEFAULT 0.0,
            total_volume REAL DEFAULT 0.0,
            last_updated REAL DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS pattern_memory (
            pattern_key   TEXT PRIMARY KEY,
            niche         TEXT DEFAULT '',
            success_rate  REAL DEFAULT 0.5,
            volume        REAL DEFAULT 0.0,
            last_seen     REAL DEFAULT 0.0,
            fatigue_score REAL DEFAULT 0.0,
            saturation_score REAL DEFAULT 0.0,
            last_seen_ts  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pattern_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_key TEXT NOT NULL,
            hit_ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hits_key_ts ON pattern_hits (pattern_key, hit_ts);

        -- Part A: accumulated counts + context sums for context-normalized causal lift
        CREATE TABLE IF NOT EXISTS causal_lift_stats (
            src_pattern     TEXT NOT NULL,
            dst_pattern     TEXT NOT NULL,
            success_w_src   INTEGER DEFAULT 0,
            exposure_w_src  INTEGER DEFAULT 0,
            success_wo_src  INTEGER DEFAULT 0,
            exposure_wo_src INTEGER DEFAULT 0,
            ctx_w_src       REAL    DEFAULT 0.0,
            ctx_wo_src      REAL    DEFAULT 0.0,
            PRIMARY KEY (src_pattern, dst_pattern)
        );

        -- Causal graph: written when lift > 1.05, conf >= 0.4, both sides >= 30 samples
        CREATE TABLE IF NOT EXISTS pattern_graph (
            src_pattern  TEXT NOT NULL,
            dst_pattern  TEXT NOT NULL,
            lift         REAL    DEFAULT 1.0,
            confidence   REAL    DEFAULT 0.0,
            samples      INTEGER DEFAULT 0,
            last_updated INTEGER DEFAULT 0,
            PRIMARY KEY (src_pattern, dst_pattern)
        );

        -- Backward compat alias
        CREATE TABLE IF NOT EXISTS pattern_affinity (
            pattern_a TEXT NOT NULL,
            pattern_b TEXT NOT NULL,
            lift  REAL DEFAULT 1.0,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (pattern_a, pattern_b)
        );

        CREATE TABLE IF NOT EXISTS niche_timeseries (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            niche TEXT NOT NULL,
            ts    INTEGER NOT NULL,
            ctr   REAL DEFAULT 0.0,
            cvr   REAL DEFAULT 0.0,
            epv   REAL DEFAULT 0.0
        );
        CREATE INDEX IF NOT EXISTS idx_ts_niche_ts ON niche_timeseries (niche, ts);

        CREATE TABLE IF NOT EXISTS niche_trend (
            niche       TEXT PRIMARY KEY,
            trend_score REAL DEFAULT 0.5,
            velocity    REAL DEFAULT 0.0,
            volatility  REAL DEFAULT 0.0,
            confidence  REAL DEFAULT 0.0,
            last_updated INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS account_memory (
            account_id   TEXT PRIMARY KEY,
            avg_roi      REAL DEFAULT 1.0,
            trend        REAL DEFAULT 0.5,
            risk_score   REAL DEFAULT 0.3,
            last_updated REAL DEFAULT 0.0
        );
    """)
    conn.commit()
    # Schema migration: add new columns to existing DBs
    for _col_sql in [
        "ALTER TABLE causal_lift_stats ADD COLUMN success_wo_src  INTEGER DEFAULT 0",
        "ALTER TABLE causal_lift_stats ADD COLUMN exposure_wo_src INTEGER DEFAULT 0",
        "ALTER TABLE causal_lift_stats ADD COLUMN ctx_w_src       REAL    DEFAULT 0.0",
        "ALTER TABLE causal_lift_stats ADD COLUMN ctx_wo_src      REAL    DEFAULT 0.0",
        "ALTER TABLE pattern_graph     ADD COLUMN last_updated    INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(_col_sql)
            conn.commit()
        except Exception:
            pass  # column already exists
    _CONN[key] = conn
    return conn


# ── Math helpers ──────────────────────────────────────────────────────────────

def _ewma(old: float, new: float, alpha: float = _ALPHA) -> float:
    return round((1 - alpha) * old + alpha * new, 6)

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    n = len(values)
    avg = sum(values) / n
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (n - 1))


# ── Part B: Bayesian confidence ───────────────────────────────────────────────

def compute_bayesian_confidence(success_rate: float, n: int) -> float:
    """
    Bayesian confidence from binomial variance.
    var = sr * (1 - sr) / max(n, 1)
    confidence = max(0, 1 - min(1, sqrt(var) * 3))

    High n + extreme sr → high confidence.
    Low n or sr ≈ 0.5  → low confidence.
    """
    sr  = _clamp(success_rate)
    var = sr * (1.0 - sr) / max(n, 1)
    return round(max(0.0, 1.0 - min(1.0, (var ** 0.5) * 3.0)), 4)


# ── Niche memory ──────────────────────────────────────────────────────────────

def update_niche_performance(niche: str, metrics: dict[str, Any]) -> None:
    if not niche:
        return
    rs  = float(metrics.get("revenue_score") or 0.5)
    ctr = float(metrics.get("ctr") or 0.0)
    cvr = float(metrics.get("cvr") or 0.0)
    vol = float(metrics.get("views") or 0.0)
    conn = _get_conn()
    row  = conn.execute("SELECT * FROM niche_memory WHERE niche=?", (niche,)).fetchone()
    if row:
        with conn:
            conn.execute(
                "UPDATE niche_memory SET avg_revenue_score=?,avg_ctr=?,avg_cvr=?,"
                "total_volume=?,last_updated=? WHERE niche=?",
                (_ewma(float(row["avg_revenue_score"]), rs),
                 _ewma(float(row["avg_ctr"]), ctr),
                 _ewma(float(row["avg_cvr"]), cvr),
                 float(row["total_volume"]) + vol,
                 time.time(), niche)
            )
    else:
        with conn:
            conn.execute("INSERT INTO niche_memory VALUES (?,?,?,?,?,?)",
                         (niche, rs, ctr, cvr, vol, time.time()))


# ── Pattern memory ────────────────────────────────────────────────────────────

def update_pattern_performance(
    pattern_key:    str,
    niche:          str,
    metrics:        dict[str, Any],
    learning_weight: float = 1.0,   # Fix 2: 0.8 when scaling to prevent feedback loop
) -> None:
    if not pattern_key:
        return
    sr  = _clamp(float(metrics.get("success_rate") or metrics.get("cvr") or 0.0))
    vol = float(metrics.get("views") or 1.0)
    alpha = _ALPHA * max(0.5, min(1.0, learning_weight))  # scale feedback dampening
    conn = _get_conn()
    row  = conn.execute(
        "SELECT * FROM pattern_memory WHERE pattern_key=?", (pattern_key,)
    ).fetchone()
    if row:
        old_sr  = float(row["success_rate"])
        new_sr  = _ewma(old_sr, sr, alpha=alpha)
        new_vol = float(row["volume"]) + vol
        old_fat = float(row["fatigue_score"])
        new_fat = _clamp(old_fat + 0.12) if new_sr < old_sr * 0.90 \
                  else _clamp(_ewma(old_fat, max(0.0, old_fat - 0.05)))
        with conn:
            conn.execute(
                "UPDATE pattern_memory SET niche=?,success_rate=?,volume=?,"
                "last_seen=?,fatigue_score=? WHERE pattern_key=?",
                (niche, new_sr, new_vol, time.time(), new_fat, pattern_key)
            )
    else:
        with conn:
            conn.execute(
                "INSERT INTO pattern_memory (pattern_key,niche,success_rate,volume,"
                "last_seen,fatigue_score,saturation_score,last_seen_ts) VALUES (?,?,?,?,?,?,?,?)",
                (pattern_key, niche, sr, vol, time.time(), 0.0, 0.0, int(time.time()))
            )


def get_top_patterns(niche: str, k: int = 5) -> list[str]:
    try:
        rows = _get_conn().execute(
            "SELECT pattern_key, success_rate, volume, fatigue_score "
            "FROM pattern_memory WHERE niche=? ORDER BY success_rate DESC LIMIT 50",
            (niche,)
        ).fetchall()
        scored = [
            (r["pattern_key"],
             float(r["success_rate"]) * math.log1p(float(r["volume"]))
             * (1.0 - float(r["fatigue_score"])))
            for r in rows
        ]
        scored.sort(key=lambda x: -x[1])
        return [pk for pk, _ in scored[:k]]
    except Exception:
        return []


def get_pattern_fatigue(pattern_key: str) -> float:
    try:
        row = _get_conn().execute(
            "SELECT fatigue_score FROM pattern_memory WHERE pattern_key=?", (pattern_key,)
        ).fetchone()
        return float(row["fatigue_score"]) if row else 0.0
    except Exception:
        return 0.0


def get_pattern_strength(pattern_key: str, niche: str = "") -> float:
    try:
        row = _get_conn().execute(
            "SELECT success_rate, volume, fatigue_score FROM pattern_memory WHERE pattern_key=?",
            (pattern_key,)
        ).fetchone()
        if not row:
            return 0.5
        sr   = float(row["success_rate"])
        vol  = float(row["volume"])
        fat  = float(row["fatigue_score"])
        raw  = sr * math.log1p(vol) * (1.0 - fat)
        ceil = math.log1p(10_000)
        return round(_clamp(raw / ceil if ceil > 0 else 0.0), 4)
    except Exception:
        return 0.5


# ── Time-decay saturation ─────────────────────────────────────────────────────

def update_pattern_saturation(
    pattern_key: str,
    timestamp:   int | None = None,
) -> None:
    if not pattern_key:
        return
    ts   = timestamp or int(time.time())
    conn = _get_conn()
    with conn:
        conn.execute("INSERT INTO pattern_hits (pattern_key, hit_ts) VALUES (?,?)",
                     (pattern_key, ts))
    with conn:
        conn.execute("DELETE FROM pattern_hits WHERE pattern_key=? AND hit_ts < ?",
                     (pattern_key, ts - 7 * 24 * 3600))


def get_pattern_saturation(pattern_key: str) -> float:
    """Returns normalised saturation score [0,1] based on hit density over last 7 days."""
    try:
        conn = _get_conn()
        now  = int(time.time())
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM pattern_hits WHERE pattern_key=? AND hit_ts >= ?",
            (pattern_key, now - 7 * 24 * 3600)
        ).fetchone()["c"]
        raw = 1.0 - math.exp(-_SAT_LAMBDA * max(0, count - _SAT_THRESHOLD))
        return round(_clamp(raw * _SAT_MAX_PENALTY), 4)
    except Exception:
        return 0.0


# ── Part A: True causal lift — proper split stats ────────────────────────────

def record_causal_observation(
    src:           str,
    dst:           str,
    a_present:     bool,
    success:       bool,
    context_score: float = 0.5,   # unified_score BEFORE pattern boost
) -> None:
    """
    Record one observation of dst running, split by whether src was active.

    a_present=True  → update with_A  side (src active alongside dst)
    a_present=False → update without_A side (src NOT active)

    Context-normalized lift:
        sr_w  = (suc_w  / exp_w)  * (ctx_w  / exp_w)
        sr_wo = (suc_wo / exp_wo) * (ctx_wo / exp_wo)
        lift  = sr_w / max(sr_wo, 1e-6)

    Safeguards:
        - Both exposures must be >= _GRAPH_MIN_SAMPLES (30)
        - confidence >= 0.4 required before writing to graph
        - lift clamped to [0.5, 1.5]
    No historical baseline is ever used.
    """
    if not src or not dst or src == dst:
        return
    ctx = _clamp(float(context_score))
    conn = _get_conn()
    row  = conn.execute(
        "SELECT success_w_src, exposure_w_src, success_wo_src, exposure_wo_src,"
        "       ctx_w_src, ctx_wo_src "
        "FROM causal_lift_stats WHERE src_pattern=? AND dst_pattern=?",
        (src, dst)
    ).fetchone()

    if row:
        suc_w  = int(row["success_w_src"])
        exp_w  = int(row["exposure_w_src"])
        suc_wo = int(row["success_wo_src"])
        exp_wo = int(row["exposure_wo_src"])
        ctx_w  = float(row["ctx_w_src"])
        ctx_wo = float(row["ctx_wo_src"])
    else:
        suc_w = exp_w = suc_wo = exp_wo = 0
        ctx_w = ctx_wo = 0.0

    if a_present:
        exp_w += 1
        suc_w += 1 if success else 0
        ctx_w += ctx
    else:
        exp_wo += 1
        suc_wo += 1 if success else 0
        ctx_wo += ctx

    with conn:
        if row:
            conn.execute(
                "UPDATE causal_lift_stats "
                "SET success_w_src=?,  exposure_w_src=?, "
                "    success_wo_src=?, exposure_wo_src=?, "
                "    ctx_w_src=?,      ctx_wo_src=? "
                "WHERE src_pattern=? AND dst_pattern=?",
                (suc_w, exp_w, suc_wo, exp_wo, ctx_w, ctx_wo, src, dst)
            )
        else:
            conn.execute(
                "INSERT INTO causal_lift_stats "
                "(src_pattern,dst_pattern,"
                " success_w_src,exposure_w_src,success_wo_src,exposure_wo_src,"
                " ctx_w_src,ctx_wo_src) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (src, dst, suc_w, exp_w, suc_wo, exp_wo, ctx_w, ctx_wo)
            )

    # Require BOTH sides to have minimum samples before computing lift
    if exp_w < _GRAPH_MIN_SAMPLES or exp_wo < _GRAPH_MIN_SAMPLES:
        return

    # Context-normalized success rates
    ctx_w  = max(ctx_w,  exp_w  * 0.3)
    ctx_wo = max(ctx_wo, exp_wo * 0.3)
    ctx_w  = min(ctx_w,  exp_w  * 1.5)
    ctx_wo = min(ctx_wo, exp_wo * 1.5)
    sr_w  = (suc_w  + 1) / (ctx_w  + 2)
    sr_wo = (suc_wo + 1) / (ctx_wo + 2)

    lift = sr_w / max(sr_wo, 1e-6)
    lift = _clamp(lift, 0.5, 1.5)       # safeguard: clamp to [0.5, 1.5]

    if lift <= _GRAPH_MIN_LIFT:
        return  # not causally significant

    # Bayesian confidence from the WITH side; require >= 0.4 to commit
    confidence = compute_bayesian_confidence(suc_w / exp_w, exp_w)
    if confidence < 0.40:
        return  # not enough statistical certainty yet

    now = int(time.time())
    g_row = conn.execute(
        "SELECT lift, samples FROM pattern_graph WHERE src_pattern=? AND dst_pattern=?",
        (src, dst)
    ).fetchone()
    with conn:
        if g_row:
            conn.execute(
                "UPDATE pattern_graph "
                "SET lift=?, confidence=?, samples=?, last_updated=? "
                "WHERE src_pattern=? AND dst_pattern=?",
                (_ewma(float(g_row["lift"]), lift, alpha=_ALPHA_GRAPH),
                 confidence, int(g_row["samples"]) + 1, now, src, dst)
            )
        else:
            conn.execute(
                "INSERT INTO pattern_graph "
                "(src_pattern,dst_pattern,lift,confidence,samples,last_updated) "
                "VALUES (?,?,?,?,?,?)",
                (src, dst, lift, confidence, 1, now)
            )


def record_causal_exposure(
    src: str, dst: str, success: bool,
) -> None:
    """Backward-compat: records as a_present=True (src was active)."""
    record_causal_observation(src, dst, a_present=True, success=success)


def get_known_src_patterns(dst: str) -> list[str]:
    """Return all src patterns that have any recorded exposure with/without for dst."""
    try:
        rows = _get_conn().execute(
            "SELECT src_pattern FROM causal_lift_stats WHERE dst_pattern=?", (dst,)
        ).fetchall()
        return [r["src_pattern"] for r in rows]
    except Exception:
        return []



def update_pattern_graph(
    src: str, dst: str, success: bool, baseline_rate: float = 0.0,
) -> None:
    """Backward-compat: records as a_present=True."""
    record_causal_observation(src, dst, a_present=True, success=success)


def update_pattern_affinity(
    pattern_a: str, pattern_b: str, success: bool, baseline_rate: float = 0.0,
) -> None:
    """Backward-compat wrapper."""
    record_causal_observation(pattern_a, pattern_b, a_present=True, success=success)


def get_pattern_confidence(dst: str, src: str = "") -> float:
    """Returns Bayesian confidence [0,1] from causal graph."""
    try:
        conn = _get_conn()
        if src:
            row = conn.execute(
                "SELECT confidence FROM pattern_graph WHERE src_pattern=? AND dst_pattern=?",
                (src, dst)
            ).fetchone()
            return float(row["confidence"]) if row else 0.0
        else:
            row = conn.execute(
                "SELECT MAX(confidence) AS c FROM pattern_graph WHERE dst_pattern=?", (dst,)
            ).fetchone()
            return float(row["c"]) if row and row["c"] is not None else 0.0
    except Exception:
        return 0.0


_BOOST_DECAY_DAYS = 14.0   # half-life for lift age decay


def get_pattern_boost(pattern_key: str, active_patterns: list[str]) -> float:
    """
    Confidence-weighted causal boost, capped at 1.25.
    Time-decay: effective_lift weighted by exp(-age_days / 14).
    Only edges with lift > 1.0, confidence >= 0.4, and positive decay contribute.
    """
    if not active_patterns:
        return 1.0
    conn  = _get_conn()
    now   = int(time.time())
    boost = 1.0
    for src in active_patterns:
        if src == pattern_key:
            continue
        row = conn.execute(
            "SELECT lift, confidence, last_updated FROM pattern_graph "
            "WHERE src_pattern=? AND dst_pattern=?",
            (src, pattern_key)
        ).fetchone()
        if not row:
            continue
        lift       = float(row["lift"])
        confidence = float(row["confidence"])
        last_upd   = int(row["last_updated"]) if row["last_updated"] else now
        if lift <= 1.0 or confidence < 0.40:
            continue
        age_days   = max(0.0, (now - last_upd) / 86400.0)
        decay      = math.exp(-age_days / _BOOST_DECAY_DAYS)
        effective  = 1.0 + (lift - 1.0) * confidence * decay
        boost     *= effective
    return round(min(1.25, max(1.0, boost)), 4)


# ── Part 2: Market phase with timeseries ──────────────────────────────────────

def update_niche_timeseries(
    niche: str, ctr: float, cvr: float, epv: float,
    timestamp: int | None = None,
) -> None:
    if not niche:
        return
    ts   = timestamp or int(time.time())
    conn = _get_conn()
    with conn:
        conn.execute(
            "INSERT INTO niche_timeseries (niche,ts,ctr,cvr,epv) VALUES (?,?,?,?,?)",
            (niche, ts, ctr, cvr, epv)
        )
    with conn:
        conn.execute("DELETE FROM niche_timeseries WHERE niche=? AND ts < ?",
                     (niche, ts - 30 * 24 * 3600))
    _recompute_niche_trend(niche, conn, ts)


def _recompute_niche_trend(niche: str, conn: sqlite3.Connection, now: int) -> None:
    """
    Part C — Early trend detection with acceleration:
        velocity     = mean(first differences of epv)
        acceleration = mean(second differences of epv)
        trend_signal = velocity + 0.5 * acceleration
        trend_score  = sigmoid(trend_signal * 10)
    """
    rows = conn.execute(
        "SELECT epv FROM niche_timeseries WHERE niche=? ORDER BY ts DESC LIMIT ?",
        (niche, _TS_WINDOW)
    ).fetchall()
    epv_series = [float(r["epv"]) for r in rows]
    n = len(epv_series)

    if n < 2:
        velocity     = 0.0
        volatility   = 0.0
        acceleration = 0.0
        confidence   = n / 10.0
    else:
        diffs      = [epv_series[i] - epv_series[i + 1] for i in range(n - 1)]
        velocity   = sum(diffs) / len(diffs)
        mean_epv   = sum(epv_series) / n
        vol_abs    = _stddev(epv_series)
        volatility = _clamp(vol_abs / max(abs(mean_epv), 1e-6))
        confidence = min(1.0, n / 10.0)
        # Part C: second differences for acceleration
        if len(diffs) >= 2:
            diffs2       = [diffs[i] - diffs[i + 1] for i in range(len(diffs) - 1)]
            acceleration = sum(diffs2) / len(diffs2)
        else:
            acceleration = 0.0

    trend_signal = velocity + 0.5 * acceleration
    trend_score  = round(_sigmoid(trend_signal * 10.0), 4)

    existing = conn.execute("SELECT 1 FROM niche_trend WHERE niche=?", (niche,)).fetchone()
    with conn:
        if existing:
            conn.execute(
                "UPDATE niche_trend SET trend_score=?,velocity=?,volatility=?,"
                "confidence=?,last_updated=? WHERE niche=?",
                (trend_score, round(velocity, 6), round(volatility, 4),
                 round(confidence, 4), now, niche)
            )
        else:
            conn.execute(
                "INSERT INTO niche_trend (niche,trend_score,velocity,volatility,"
                "confidence,last_updated) VALUES (?,?,?,?,?,?)",
                (niche, trend_score, round(velocity, 6),
                 round(volatility, 4), round(confidence, 4), now)
            )


def get_market_phase(niche: str) -> tuple[str, float]:
    """Returns (phase, confidence). phase ∈ {rising, neutral, declining}."""
    try:
        row = _get_conn().execute(
            "SELECT trend_score, confidence FROM niche_trend WHERE niche=?", (niche,)
        ).fetchone()
        if not row:
            return ("neutral", 0.0)
        trend = float(row["trend_score"])
        conf  = float(row["confidence"])
        if trend > 0.60:
            return ("rising", conf)
        if trend < 0.40:
            return ("declining", conf)
        return ("neutral", conf)
    except Exception:
        return ("neutral", 0.0)


def get_niche_trend(niche: str) -> float:
    try:
        row = _get_conn().execute(
            "SELECT trend_score FROM niche_trend WHERE niche=?", (niche,)
        ).fetchone()
        return float(row["trend_score"]) if row else 0.5
    except Exception:
        return 0.5


def update_niche_trend(
    niche: str, views: float, conversions: float, timestamp: int | None = None
) -> None:
    """Backward-compat stub → update_niche_timeseries."""
    if not niche or views <= 0:
        return
    update_niche_timeseries(niche, ctr=0.0, cvr=0.0, epv=conversions / views,
                            timestamp=timestamp)


# ── Part 5: Per-pattern explore rate with safety floor ────────────────────────

def get_explore_rate_for_pattern(
    pattern_key: str,
    phase:       str   = "neutral",
    confidence:  float = 0.0,
) -> float:
    """
    base = {rising:0.05, neutral:0.10, declining:0.20}[phase]
    rate = base + (1 - confidence) * 0.15

    Fix 3 — Safety floor: confidence < 0.3 → rate >= 0.20
    """
    phase_base: dict[str, float] = {
        "rising": 0.05, "neutral": 0.10, "declining": 0.20,
    }
    base = phase_base.get(phase, 0.10)
    rate = base + (1.0 - _clamp(confidence)) * 0.15
    if confidence < 0.30:
        rate = max(rate, 0.20)
    return round(_clamp(rate, 0.0, 0.35), 4)


# ── Account memory ────────────────────────────────────────────────────────────

def update_account_performance(account_id: str, metrics: dict[str, Any]) -> None:
    if not account_id:
        return
    roi  = float(metrics.get("roi") or metrics.get("historical_roi") or 1.0)
    risk = _clamp(float(metrics.get("risk_score") or 0.3))
    conn     = _get_conn()
    row      = conn.execute(
        "SELECT * FROM account_memory WHERE account_id=?", (account_id,)
    ).fetchone()
    roi_norm = _clamp(roi / 2.0)
    if row:
        with conn:
            conn.execute(
                "UPDATE account_memory SET avg_roi=?,trend=?,risk_score=?,last_updated=?"
                " WHERE account_id=?",
                (_ewma(float(row["avg_roi"]), roi),
                 _ewma(float(row["trend"]), roi_norm),
                 _ewma(float(row["risk_score"]), risk),
                 time.time(), account_id)
            )
    else:
        with conn:
            conn.execute("INSERT INTO account_memory VALUES (?,?,?,?,?)",
                         (account_id, roi, roi_norm, risk, time.time()))


def get_account_trend(account_id: str) -> float:
    try:
        row = _get_conn().execute(
            "SELECT trend FROM account_memory WHERE account_id=?", (account_id,)
        ).fetchone()
        return float(row["trend"]) if row else 0.5
    except Exception:
        return 0.5
