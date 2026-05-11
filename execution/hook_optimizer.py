"""
execution/hook_optimizer.py — Hook Optimization Layer.

Generates multiple hook variations for a piece of content, scores each
using the existing content_decision scoring logic, and returns the
highest-scoring variation for publishing.

A "hook" is the opening line / title of a caption that determines
whether a viewer stops scrolling.

Public API:
    optimize_hook(candidate, niche, platform)    → HookResult
    generate_variations(base_caption, niche, n)  → list[str]
    score_hook(hook_text, niche, platform)       → float

Design:
  - Hook templates are niche-aware (60+ templates across 6 niches).
  - Scoring delegates to content_decision.score_candidate() when available;
    falls back to a lightweight internal scorer.
  - Deterministic when seed is provided (for reproducibility).
  - Never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.hook_optimizer")

# ── Hook template library ─────────────────────────────────────────────────────
# Format: {topic} is replaced by niche-specific keywords extracted from caption.

_HOOK_TEMPLATES: dict[str, list[str]] = {
    "tech": [
        "This {topic} trick will save you HOURS ⚡",
        "Nobody talks about this {topic} hack 🤫",
        "I tested every {topic} method — here's the winner",
        "Stop doing {topic} the wrong way 🚫",
        "The {topic} secret nobody tells you 🔓",
        "This changed how I use {topic} forever",
        "POV: You just discovered the best {topic} tool",
        "Wait until you see what {topic} can actually do 👀",
    ],
    "fitness": [
        "The {topic} exercise everyone skips (huge mistake)",
        "I tried {topic} for 30 days — here's what happened",
        "Stop wasting time on {topic} — do this instead",
        "This {topic} tip got me results in 2 weeks 💪",
        "The real reason your {topic} isn't working",
        "Trainers don't want you to know this {topic} secret",
        "POV: You finally figured out {topic}",
        "{topic} hack that actually works (no equipment needed)",
    ],
    "finance": [
        "How I made money with {topic} (step by step)",
        "The {topic} mistake that cost me thousands 💸",
        "Nobody tells you this about {topic} income",
        "This {topic} strategy changed everything for me",
        "Stop leaving money on the table with {topic}",
        "The {topic} truth they don't want you to know",
        "I tested {topic} for 90 days — real results",
        "Simple {topic} trick = passive income 💰",
    ],
    "entertainment": [
        "Wait for it... 😂",
        "POV: {topic} hits different at 2am",
        "Tell me you're a {topic} fan without telling me",
        "This {topic} moment is too real 💀",
        "Nobody prepared me for this {topic} energy",
        "The {topic} era we all needed",
        "If you know {topic}, you know 👀",
        "Okay but why does {topic} always do this 😭",
    ],
    "food": [
        "The {topic} recipe that broke the internet 🔥",
        "I ate {topic} every day for a week — here's what happened",
        "This {topic} hack will change your cooking forever",
        "The easiest {topic} you'll ever make (5 minutes)",
        "Stop buying {topic} — make it at home instead",
        "My mom's secret {topic} recipe (finally sharing it)",
        "Gordon Ramsay would approve this {topic} trick",
        "This {topic} combination sounds wrong but tastes amazing",
    ],
    "travel": [
        "This {topic} spot is better than Instagram shows 📍",
        "Locals don't want you to know about this {topic}",
        "I spent $50 in {topic} for a week — here's how",
        "The real {topic} experience vs what you see online",
        "Hidden {topic} gem that will blow your mind 🌍",
        "Why {topic} should be your next destination",
        "Things nobody tells you before visiting {topic}",
        "I almost skipped {topic} — biggest mistake I almost made",
    ],
}

_DEFAULT_TEMPLATES = [
    "You won't believe this {topic} trick 🔥",
    "The {topic} secret nobody shares",
    "Stop doing {topic} wrong — do this instead",
    "This {topic} changed everything for me",
    "POV: you finally understand {topic} 👀",
    "Wait until you see this {topic} hack",
    "{topic} tip that actually works (tested)",
    "I wish someone told me this about {topic} earlier",
]

# Curiosity keywords boost hook score
_CURIOSITY_WORDS = {
    "secret", "hack", "trick", "nobody", "hidden", "stop", "mistake",
    "change", "best", "worst", "finally", "real", "truth", "exposed",
    "wait", "pov", "caught", "shocking", "never", "always", "only",
}

# Emoji boost signals high engagement potential
_POWER_EMOJI = {"🔥", "💀", "😂", "👀", "💸", "💪", "🤫", "🚫", "⚡", "🌍", "📍"}


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class HookResult:
    best_hook:    str
    best_score:   float
    all_hooks:    list[str]              = field(default_factory=list)
    all_scores:   list[float]           = field(default_factory=list)
    niche:        str                   = ""
    topic:        str                   = ""
    method:       str                   = "internal"   # "content_decision" | "internal"
    meta:         dict[str, Any]        = field(default_factory=dict)


# ── Feedback learning store ───────────────────────────────────────────────────

_DEFAULT_HOOK_DB = Path("data") / "hook_feedback.db"
_HOOK_DDL = """
CREATE TABLE IF NOT EXISTS hook_performance (
    hook_hash       TEXT PRIMARY KEY,
    hook_text       TEXT NOT NULL DEFAULT '',
    niche           TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT '',
    impressions     INTEGER NOT NULL DEFAULT 0,
    views           INTEGER NOT NULL DEFAULT 0,
    likes           INTEGER NOT NULL DEFAULT 0,
    comments        INTEGER NOT NULL DEFAULT 0,
    ctr_ema         REAL NOT NULL DEFAULT 0.0,
    eng_ema         REAL NOT NULL DEFAULT 0.0,
    composite_ema   REAL NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    first_seen      REAL NOT NULL DEFAULT 0.0,
    last_updated    REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS winning_patterns (
    pattern         TEXT PRIMARY KEY,
    niche           TEXT NOT NULL DEFAULT '',
    avg_score       REAL NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    last_seen       REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_hp_niche ON hook_performance(niche, composite_ema DESC);
CREATE INDEX IF NOT EXISTS idx_wp_niche ON winning_patterns(niche, avg_score DESC);
"""

_hook_local     = threading.local()
_hook_init_lock = threading.Lock()


def _hook_db_path() -> Path:
    env = os.environ.get("HOOK_FEEDBACK_DB")
    return Path(env) if env else _DEFAULT_HOOK_DB


def _hook_conn() -> sqlite3.Connection:
    if not hasattr(_hook_local, "conn") or _hook_local.conn is None:
        db = _hook_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        with _hook_init_lock:
            con.executescript(_HOOK_DDL)
            con.commit()
        _hook_local.conn = con
    return _hook_local.conn


def _hook_exec(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    c = _hook_conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur


def record_hook_performance(
    hook:        str,
    niche:       str,
    platform:    str   = "tiktok",
    views:       int   = 0,
    likes:       int   = 0,
    comments:    int   = 0,
    impressions: int   = 0,
    ctr:         float = 0.0,
) -> None:
    """
    Feed real performance data back into the hook learning store.

    Call this after metrics are collected for a published post.
    EMA is updated to smooth out outlier posts.
    """
    h = hook.strip()
    if not h:
        return
    hook_hash = hashlib.sha256(h.encode()).hexdigest()[:16]
    alpha     = 0.30

    eng = (likes + comments) / max(1, views)
    _ctr = ctr if ctr > 0 else (views / max(1, impressions) if impressions > 0 else 0.0)
    composite = 0.6 * min(1.0, _ctr / 0.20) + 0.4 * min(1.0, eng / 0.10)

    try:
        row = _hook_conn().execute(
            "SELECT * FROM hook_performance WHERE hook_hash=?", (hook_hash,)
        ).fetchone()

        if row:
            n        = int(row["sample_count"]) + 1
            new_ctr  = row["ctr_ema"] * (1 - alpha) + _ctr * alpha
            new_eng  = row["eng_ema"] * (1 - alpha) + eng  * alpha
            new_comp = row["composite_ema"] * (1 - alpha) + composite * alpha
            _hook_exec(
                "UPDATE hook_performance SET impressions=impressions+?, views=views+?,"
                " likes=likes+?, comments=comments+?, ctr_ema=?, eng_ema=?,"
                " composite_ema=?, sample_count=?, last_updated=?"
                " WHERE hook_hash=?",
                (impressions, views, likes, comments,
                 round(new_ctr, 5), round(new_eng, 5),
                 round(new_comp, 5), n, time.time(), hook_hash),
            )
        else:
            _hook_exec(
                "INSERT INTO hook_performance"
                " (hook_hash, hook_text, niche, platform, impressions, views, likes,"
                "  comments, ctr_ema, eng_ema, composite_ema, sample_count, first_seen, last_updated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (hook_hash, h[:300], niche, platform,
                 impressions, views, likes, comments,
                 round(_ctr, 5), round(eng, 5), round(composite, 5),
                 time.time(), time.time()),
            )

        if composite >= 0.55:
            _update_winning_patterns(h, niche, composite)

        LOGGER.debug("hook_feedback_recorded hash=%s composite=%.3f", hook_hash, composite)
    except Exception as exc:
        LOGGER.warning("hook_feedback_error error=%s", exc)


def _update_winning_patterns(hook: str, niche: str, score: float) -> None:
    words = hook.lower().split()
    patterns = []
    if len(words) >= 3:
        patterns.append(" ".join(words[:3]))
    for i in range(len(words) - 1):
        if words[i] in _CURIOSITY_WORDS or words[i + 1] in _CURIOSITY_WORDS:
            patterns.append(f"{words[i]} {words[i+1]}")

    alpha = 0.20
    for pat in patterns:
        try:
            row = _hook_conn().execute(
                "SELECT avg_score, sample_count FROM winning_patterns WHERE pattern=? AND niche=?",
                (pat, niche),
            ).fetchone()
            if row:
                new_avg = row["avg_score"] * (1 - alpha) + score * alpha
                _hook_exec(
                    "UPDATE winning_patterns SET avg_score=?, sample_count=sample_count+1, last_seen=?"
                    " WHERE pattern=? AND niche=?",
                    (round(new_avg, 5), time.time(), pat, niche),
                )
            else:
                _hook_exec(
                    "INSERT INTO winning_patterns (pattern, niche, avg_score, sample_count, last_seen)"
                    " VALUES (?,?,?,1,?)",
                    (pat, niche, round(score, 5), time.time()),
                )
        except Exception:
            pass


def _load_winning_patterns(niche: str, limit: int = 20) -> list[str]:
    try:
        rows = _hook_conn().execute(
            "SELECT pattern FROM winning_patterns WHERE niche=? AND sample_count >= 2"
            " ORDER BY avg_score DESC LIMIT ?",
            (niche, limit),
        ).fetchall()
        return [r["pattern"] for r in rows]
    except Exception:
        return []


def get_top_hooks(
    niche:    str,
    platform: str = "tiktok",
    limit:    int = 10,
) -> list[dict]:
    """Return historically best-performing hooks for a niche."""
    try:
        rows = _hook_conn().execute(
            "SELECT hook_text, composite_ema, ctr_ema, eng_ema, sample_count"
            " FROM hook_performance"
            " WHERE niche=? AND platform=? AND sample_count >= 2"
            " ORDER BY composite_ema DESC LIMIT ?",
            (niche, platform, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

# ── Topic extraction ──────────────────────────────────────────────────────────

def _extract_topic(caption: str, niche: str) -> str:
    """
    Extract the main topic word from a caption.
    Falls back to niche name if nothing found.
    """
    # Try to find a noun-like word (2+ syllables, not a stop word)
    _STOP = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
              "for", "of", "with", "this", "that", "is", "are", "was", "be"}
    words = re.findall(r"\b[a-zA-Z]{4,}\b", caption.lower())
    for w in words:
        if w not in _STOP:
            return w
    return niche


# ── Internal hook scorer ──────────────────────────────────────────────────────

def _score_hook_internal(hook: str) -> float:
    """
    Lightweight hook scorer. Range [0, 1].

    Signals:
      - curiosity word presence   (0.35 weight)
      - power emoji presence      (0.20 weight)
      - length sweet spot 40–80ch (0.20 weight)
      - question or ellipsis      (0.15 weight)
      - caps word (not all-caps)  (0.10 weight)
    """
    h = hook.strip()
    words_lower = set(re.findall(r"\b\w+\b", h.lower()))

    curiosity = min(1.0, sum(1 for w in _CURIOSITY_WORDS if w in words_lower) / 3)
    emoji_hit = 1.0 if any(e in h for e in _POWER_EMOJI) else 0.0
    length_ok = 1.0 if 35 <= len(h) <= 90 else max(0.0, 1.0 - abs(len(h) - 62) / 62)
    has_hook  = 1.0 if ("?" in h or "..." in h or h.endswith("😂") or h.endswith("👀")) else 0.3
    caps_word = 1.0 if re.search(r"\b[A-Z]{2,}\b", h) else 0.4

    score = (
        0.35 * curiosity +
        0.20 * emoji_hit +
        0.20 * length_ok +
        0.15 * has_hook  +
        0.10 * caps_word
    )
    return round(min(1.0, score), 4)


def _score_hook_via_decision(hook: str, niche: str, platform: str) -> float | None:
    """
    Delegate hook scoring to content_decision.score_candidate().
    Returns None if content_decision is unavailable.
    """
    try:
        from core.content_decision import ContentCandidate, score_candidate
        cand = ContentCandidate(
            item_id         = hashlib.sha256(hook.encode()).hexdigest()[:8],
            trend_score     = 0.6,
            product_intent  = 0.5,
            hook_potential  = _score_hook_internal(hook),
            match_score     = 0.7,
            novelty_score   = 0.6,
            production_cost = 0.15,
        )
        return score_candidate(cand, mode="reup", niche=niche)
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def generate_variations(
    base_caption: str,
    niche:        str,
    n:            int = 8,
    seed:         int | None = None,
    use_learned:  bool = True,
) -> list[str]:
    """
    Generate N hook variations for a caption.

    V2: When use_learned=True (default), prepends historically high-performing
    hooks from the feedback store to bias selection toward proven patterns.

    Combines (in priority order):
      1. Historically winning hooks (from feedback DB — real data)
      2. Winning structural patterns expanded with current topic
      3. Niche-specific templates
      4. Generic power templates
      5. Cleaned original caption

    Returns exactly N hooks (deduplicated).
    """
    rng   = random.Random(seed)
    topic = _extract_topic(base_caption, niche)

    templates     = _HOOK_TEMPLATES.get(niche, _DEFAULT_TEMPLATES)
    all_templates = templates + _DEFAULT_TEMPLATES
    rng.shuffle(all_templates)

    hooks: list[str] = []
    seen:  set[str]  = set()

    # 1. Historically winning hooks (actual text, already proven)
    if use_learned:
        top = get_top_hooks(niche, limit=n // 2)
        for entry in top:
            h = entry.get("hook_text", "").strip()
            if h and h not in seen:
                hooks.append(h)
                seen.add(h)

        # 2. Winning pattern starters expanded with current topic
        winning_pats = _load_winning_patterns(niche, limit=5)
        for pat in winning_pats:
            hook = f"{pat.capitalize()} {topic}".strip()
            if hook not in seen and len(hooks) < n - 2:
                hooks.append(hook)
                seen.add(hook)

    # 3+4. Template-based hooks
    for tmpl in all_templates:
        hook = tmpl.replace("{topic}", topic).strip()
        if hook not in seen:
            hooks.append(hook)
            seen.add(hook)
        if len(hooks) >= n - 1:
            break

    # 5. Always include the original caption (first sentence, cleaned)
    original = base_caption.split("\n")[0].strip()[:80]
    if original and original not in seen:
        hooks.append(original)

    # Pad with generic if short
    while len(hooks) < n:
        fb = rng.choice(_DEFAULT_TEMPLATES).replace("{topic}", topic)
        if fb not in seen:
            hooks.append(fb)
            seen.add(fb)

    return hooks[:n]


def score_hook(hook: str, niche: str = "", platform: str = "tiktok") -> float:
    """
    Score a single hook string. Returns float in [0, 1].
    Tries content_decision first; falls back to internal scorer.
    """
    cd_score = _score_hook_via_decision(hook, niche, platform)
    if cd_score is not None:
        return cd_score
    return _score_hook_internal(hook)


def optimize_hook(
    candidate:    dict[str, Any],
    niche:        str = "",
    platform:     str = "tiktok",
    n_variations: int = 8,
    seed:         int | None = None,
    use_learned:  bool = True,
) -> HookResult:
    """
    Generate and score N hook variations. Returns the best one.

    V2: Integrates feedback-learned patterns for biased generation.
      - use_learned=True (default): proven hooks appear first in pool
      - result.meta["from_learned"] indicates if best pick was from history
      - Call record_hook_performance() after publish to close the loop

    candidate: content candidate dict (must have 'caption' key at minimum)
    niche:     override niche (falls back to candidate['niche'])
    platform:  "tiktok" | "facebook"

    Returns HookResult with best_hook, best_score, and full rankings.
    Never raises.
    """
    try:
        _niche   = niche or candidate.get("niche", "entertainment")
        _caption = candidate.get("caption", "") or candidate.get("source_url", "")
        _topic   = _extract_topic(_caption, _niche)

        variations = generate_variations(
            _caption, _niche, n=n_variations, seed=seed, use_learned=use_learned
        )
        scores: list[float] = []
        method = "internal"

        for hook in variations:
            cd = _score_hook_via_decision(hook, _niche, platform)
            if cd is not None:
                method = "content_decision"
                scores.append(cd)
            else:
                scores.append(_score_hook_internal(hook))

        best_idx   = scores.index(max(scores))
        best_hook  = variations[best_idx]
        best_score = scores[best_idx]

        # Track if this came from the learned pool
        learned_texts = {e.get("hook_text", "") for e in get_top_hooks(_niche, limit=n_variations)}
        from_learned  = best_hook in learned_texts

        LOGGER.info(
            "hook_optimized niche=%s best_score=%.3f method=%s learned=%s hook=%.50s",
            _niche, best_score, method, from_learned, best_hook,
        )
        return HookResult(
            best_hook  = best_hook,
            best_score = best_score,
            all_hooks  = variations,
            all_scores = scores,
            niche      = _niche,
            topic      = _topic,
            method     = method,
            meta       = {"from_learned": from_learned, "platform": platform},
        )
    except Exception as exc:
        LOGGER.warning("hook_optimize_error error=%s", exc)
        fallback = candidate.get("caption", "Check this out!")[:80]
        return HookResult(
            best_hook  = fallback,
            best_score = 0.3,
            niche      = niche,
            method     = "fallback",
            meta       = {"error": str(exc)},
        )
