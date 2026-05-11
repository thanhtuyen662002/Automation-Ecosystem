"""
core/attribution_engine.py — Revenue Attribution Layer

Maps revenue → content_id → account → page → niche with real click +
conversion data, replacing proxy estimates with measured profit signals.

Pipeline position:
    Content produced
        ↓  generate_tracking_code()   → embed in affiliate link / bio / comment
    User clicks
        ↓  record_click()
    User converts
        ↓  record_conversion()
    TRACK stage
        ↓  flush_to_profit_engine()  → profit_engine.update_profit(REAL_revenue)

Tracking code format:
    aff://{content_id}:{page_id}:{timestamp_hex}

Multi-touch attribution:
    If N touches led to a conversion:
        last-click touch   → 70% of revenue (PRIMARY)
        remaining touches  → share 30% equally (ASSIST)

Design contracts:
  - Deterministic: same inputs → same tracking_code
  - Exception-safe: all methods return safe defaults on error
  - Persistent: all state in attribution_store (SQLite WAL)
  - In-process log: _ATTR_LOG ring buffer (audit only, non-critical)
  - Zero new dependencies (stdlib only)

Public API:
    generate_tracking_code(content_id, page_id, timestamp) -> str
    parse_tracking_code(code)                              -> dict | None
    record_click(tracking_code, ...)                       -> bool
    record_conversion(tracking_code, revenue, ...)         -> bool
    flush_to_profit_engine()                               -> int   (n attributed)
    get_revenue(content_id, niche)                         -> float
    get_conversion_rate(content_id, niche)                 -> float
    get_profit(content_id, niche, cost)                    -> float
    get_attribution_report(content_id, niche)              -> dict
    reset_attribution_state()                              # testing only
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

LOGGER = logging.getLogger("core.attribution_engine")

# ── Constants ──────────────────────────────────────────────────────────────────

# Multi-touch split
_LAST_CLICK_SHARE: float = 0.70   # last-touch → 70%
_ASSIST_SHARE:     float = 0.30   # all other touches share 30%

# Tracking code scheme
_SCHEME = "aff"
_CODE_RE = re.compile(
    r"^aff://([^:]+):([^:]+):([0-9a-f]+)$", re.IGNORECASE
)

# In-process audit log (ring buffer, non-critical)
_ATTR_LOG:     list[dict[str, Any]] = []
_MAX_LOG_SIZE: int = 2_000


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_store():
    from core.attribution_store import get_attribution_store
    return get_attribution_store()


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── Part 1: Tracking Code System ─────────────────────────────────────────────

def generate_tracking_code(
    content_id: str,
    page_id:    str,
    timestamp:  float | None = None,
) -> str:
    """
    Generate a unique, deterministic tracking code for a piece of content.

    Format:  aff://{content_id}:{page_id}:{timestamp_hex}

    The tracking code is embedded into:
        - Affiliate link query params  (e.g. ?ref=aff%3A%2F%2F...)
        - Link-in-bio URL
        - Pinned comment text

    Args:
        content_id: unique content identifier
        page_id:    TikTok / platform page ID or account handle
        timestamp:  unix timestamp (default: now)

    Returns:
        str — tracking code, e.g.  "aff://vid123:page456:6780f1a0"
    """
    ts_hex = format(int(timestamp or time.time()), "x")
    # Sanitise: replace : and / which would break the format
    cid = content_id.replace(":", "_").replace("/", "_")
    pid = page_id.replace(":", "_").replace("/", "_")
    return f"{_SCHEME}://{cid}:{pid}:{ts_hex}"


def parse_tracking_code(code: str) -> dict[str, Any] | None:
    """
    Parse a tracking code back into its component parts.

    Returns:
        {"content_id": str, "page_id": str, "timestamp": float}
        or None if code is malformed.
    """
    if not code:
        return None
    m = _CODE_RE.match(code.strip())
    if m is None:
        return None
    try:
        return {
            "content_id": m.group(1),
            "page_id":    m.group(2),
            "timestamp":  float(int(m.group(3), 16)),
        }
    except (ValueError, OverflowError):
        return None


# ── Part 2: Click + Conversion Tracking ──────────────────────────────────────

def record_click(
    tracking_code: str,
    niche:         str   = "",
    account_id:    str   = "",
    click_ts:      float | None = None,
) -> bool:
    """
    Record a click event for a tracking code.

    The tracking code is parsed to extract content_id + page_id.
    A touch record is inserted into the attribution store.

    Args:
        tracking_code: code embedded in the affiliate link
        niche:         content niche (for attribution grouping)
        account_id:    platform account that posted this content
        click_ts:      click timestamp (default: now)

    Returns:
        True if recorded successfully, False on any error.
    """
    parsed = parse_tracking_code(tracking_code)
    if parsed is None:
        LOGGER.warning("attribution_engine record_click invalid_code code=%r", tracking_code)
        return False

    content_id = parsed["content_id"]
    page_id    = parsed["page_id"]
    origin_ts  = parsed["timestamp"]
    now        = click_ts or time.time()

    try:
        store = _get_store()
        store.insert_touch(
            tracking_code=tracking_code,
            content_id=content_id,
            page_id=page_id,
            niche=niche,
            account_id=account_id,
            origin_ts=origin_ts,
            click_ts=now,
        )
        LOGGER.debug(
            "attribution_engine click content=%s page=%s niche=%s",
            content_id, page_id, niche,
        )
        return True
    except Exception as exc:
        LOGGER.warning("attribution_engine record_click_failed error=%s", exc)
        return False


def record_conversion(
    tracking_code: str,
    revenue:       float,
    niche:         str   = "",
    account_id:    str   = "",
    conversion_ts: float | None = None,
) -> bool:
    """
    Record a conversion (purchase / affiliate commission) for a tracking code.

    A pending conversion record is inserted into the store. It will be
    attributed during the next flush_to_profit_engine() call.

    Args:
        tracking_code: code from the affiliate link that converted
        revenue:       actual revenue generated (USD or proxy units)
        niche:         content niche
        account_id:    posting account
        conversion_ts: time of conversion (default: now)

    Returns:
        True if recorded successfully, False on any error.
    """
    parsed = parse_tracking_code(tracking_code)
    if parsed is None:
        LOGGER.warning(
            "attribution_engine record_conversion invalid_code code=%r", tracking_code
        )
        return False

    content_id = parsed["content_id"]
    page_id    = parsed["page_id"]
    origin_ts  = parsed["timestamp"]
    revenue    = max(0.0, revenue)
    now        = conversion_ts or time.time()

    try:
        store = _get_store()
        store.insert_conversion(
            tracking_code=tracking_code,
            content_id=content_id,
            page_id=page_id,
            niche=niche,
            account_id=account_id,
            revenue=revenue,
            origin_ts=origin_ts,
            conversion_ts=now,
        )
        LOGGER.debug(
            "attribution_engine conversion content=%s page=%s revenue=%.4f",
            content_id, page_id, revenue,
        )
        return True
    except Exception as exc:
        LOGGER.warning("attribution_engine record_conversion_failed error=%s", exc)
        return False


# ── Part 3: Multi-Touch Attribution ──────────────────────────────────────────

def _attribute_conversion(conv: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Apply multi-touch attribution to a single conversion event.

    Touch collection:
        Fetches ALL touches for the converting content's content_id (across
        all tracking codes), ordered chronologically. This captures assists
        that may have been recorded under different codes.

    Rules:
        0 touches → 100% credit to converting content (direct attribution)
        1 touch   → 100% to that single touch
        N touches → last-click = 70%, assists share 30% equally

    Returns a list of attribution dicts:
        [{"content_id", "niche", "page_id", "account_id",
          "revenue_share", "touch_role": "last_click"|"assist"}]
    """
    store      = _get_store()
    conv_cid   = conv["content_id"]
    rev        = conv["revenue"]
    conv_niche = conv["niche"]

    # Fetch ALL touches for the converting content_id (cross-code journey)
    touches = store.get_touches_by_content(conv_cid)

    if not touches:
        # No touch history — give all credit to the converting content directly
        return [{
            "content_id":    conv_cid,
            "niche":         conv_niche,
            "page_id":       conv["page_id"],
            "account_id":    conv["account_id"],
            "revenue_share": rev,
            "touch_role":    "last_click",
        }]

    attributions: list[dict[str, Any]] = []

    if len(touches) == 1:
        t = touches[0]
        attributions.append({
            "content_id":    t["content_id"],
            "niche":         conv_niche or t["niche"],
            "page_id":       t["page_id"],
            "account_id":    t["account_id"],
            "revenue_share": rev,
            "touch_role":    "last_click",
        })
    else:
        # Last touch (most recent): 70%
        last = touches[-1]
        attributions.append({
            "content_id":    last["content_id"],
            "niche":         conv_niche or last["niche"],
            "page_id":       last["page_id"],
            "account_id":    last["account_id"],
            "revenue_share": rev * _LAST_CLICK_SHARE,
            "touch_role":    "last_click",
        })
        # All earlier touches: share 30% equally (assist)
        assists    = touches[:-1]
        assist_per = (rev * _ASSIST_SHARE) / len(assists)
        for t in assists:
            attributions.append({
                "content_id":    t["content_id"],
                "niche":         conv_niche or t["niche"],
                "page_id":       t["page_id"],
                "account_id":    t["account_id"],
                "revenue_share": assist_per,
                "touch_role":    "assist",
            })

    return attributions


def flush_to_profit_engine(cost_map: dict[str, float] | None = None) -> int:
    """
    Process all pending conversions: apply multi-touch attribution, accumulate
    results in attr_results, then call profit_engine.update_profit() with
    REAL attributed revenue.

    This closes the loop from measured revenue → profit_engine → decision layer.

    Args:
        cost_map: optional dict {content_id: cost} for profit calculation.
                  Defaults to 0.0 cost if not provided.

    Returns:
        Number of conversions attributed in this flush.
    """
    store     = _get_store()
    pending   = store.get_pending_conversions()
    if not pending:
        return 0

    attributed_ids:  list[int]                       = []
    # Accumulate per (content_id, niche) before writing to store
    acc: dict[tuple[str, str], dict[str, Any]] = {}

    for conv in pending:
        try:
            attrs = _attribute_conversion(conv)
        except Exception as exc:
            LOGGER.warning(
                "attribution_engine _attribute_conversion failed id=%s error=%s",
                conv["id"], exc,
            )
            continue

        for a in attrs:
            k = (a["content_id"], a["niche"])
            if k not in acc:
                acc[k] = {
                    "content_id":  a["content_id"],
                    "niche":       a["niche"],
                    "page_id":     a["page_id"],
                    "account_id":  a["account_id"],
                    "clicks":      0,
                    "conversions": 0,
                    "attr_rev":    0.0,
                    "asst_rev":    0.0,
                }
            if a["touch_role"] == "last_click":
                acc[k]["conversions"] += 1
                acc[k]["attr_rev"]    += a["revenue_share"]
                # count click for last-click touch
                acc[k]["clicks"] += 1
            else:
                acc[k]["asst_rev"] += a["revenue_share"]
                acc[k]["clicks"]   += 1

        attributed_ids.append(conv["id"])

    # ── Write attribution results + call profit_engine ──────────────────────
    for (cid, niche), a in acc.items():
        # Store attribution summary
        store.upsert_attr_result(
            content_id=cid,
            niche=niche,
            page_id=a["page_id"],
            account_id=a["account_id"],
            delta_clicks=a["clicks"],
            delta_conv=a["conversions"],
            delta_attr_rev=a["attr_rev"],
            delta_asst_rev=a["asst_rev"],
        )

        # Propagate real revenue to profit_engine
        try:
            from core.profit_engine import update_profit as _up
            cost = (cost_map or {}).get(cid, 0.0)
            total_rev = a["attr_rev"] + a["asst_rev"]
            _up(content_id=cid, niche=niche, revenue=total_rev, cost=cost)
        except Exception as exc:
            LOGGER.warning(
                "attribution_engine profit_engine_update_failed cid=%s error=%s",
                cid, exc,
            )

        # Audit log
        entry = {
            "content_id":  cid,
            "niche":       niche,
            "clicks":      a["clicks"],
            "conversions": a["conversions"],
            "attr_rev":    round(a["attr_rev"], 4),
            "asst_rev":    round(a["asst_rev"], 4),
            "total_rev":   round(a["attr_rev"] + a["asst_rev"], 4),
        }
        _ATTR_LOG.append(entry)
        if len(_ATTR_LOG) > _MAX_LOG_SIZE:
            del _ATTR_LOG[: len(_ATTR_LOG) - _MAX_LOG_SIZE]

        LOGGER.debug(
            "attribution_engine flush cid=%s niche=%s "
            "rev=%.4f conv=%d clicks=%d",
            cid, niche, a["attr_rev"] + a["asst_rev"],
            a["conversions"], a["clicks"],
        )

    # Mark as attributed
    store.mark_attributed(attributed_ids)
    return len(attributed_ids)


# ── Part 4: Public Query API ──────────────────────────────────────────────────

def get_revenue(content_id: str, niche: str = "") -> float:
    """
    Return total attributed revenue for a content item.

    Aggregates last-click (70%) + assist (30%) shares.

    Args:
        content_id: content identifier
        niche:      optional niche filter (empty = all niches aggregated)

    Returns:
        float — total attributed revenue, 0.0 if unknown
    """
    try:
        store = _get_store()
        ar    = store.get_attr_result(content_id, niche)
        return float(ar["total_rev"]) if ar else 0.0
    except Exception as exc:
        LOGGER.warning("attribution_engine get_revenue_failed cid=%s error=%s", content_id, exc)
        return 0.0


def get_conversion_rate(content_id: str, niche: str = "") -> float:
    """
    Return conversion rate: conversions / clicks.

    Returns:
        float [0, 1] — conversion rate, 0.0 if no clicks
    """
    try:
        store = _get_store()
        ar    = store.get_attr_result(content_id, niche)
        if ar is None or ar["clicks"] == 0:
            return 0.0
        return _clamp(ar["conversions"] / ar["clicks"])
    except Exception as exc:
        LOGGER.warning(
            "attribution_engine get_conversion_rate_failed cid=%s error=%s",
            content_id, exc,
        )
        return 0.0


def get_profit(content_id: str, niche: str = "", cost: float = 0.0) -> float:
    """
    Return measured profit: total_revenue - cost.

    Args:
        content_id: content identifier
        niche:      optional niche filter
        cost:       actual production cost

    Returns:
        float — profit (can be negative)
    """
    return get_revenue(content_id, niche) - max(0.0, cost)


def get_attribution_report(content_id: str, niche: str = "") -> dict[str, Any]:
    """
    Return a full attribution report for a content item.

    Returns:
        dict with: content_id, niche, clicks, conversions,
                   conversion_rate, attributed_rev, assist_rev,
                   total_rev, profit (cost=0)
    """
    try:
        store = _get_store()
        ar    = store.get_attr_result(content_id, niche)
    except Exception as exc:
        LOGGER.warning(
            "attribution_engine get_report_failed cid=%s error=%s", content_id, exc
        )
        ar = None

    if ar is None:
        return {
            "content_id":      content_id,
            "niche":           niche,
            "clicks":          0,
            "conversions":     0,
            "conversion_rate": 0.0,
            "attributed_rev":  0.0,
            "assist_rev":      0.0,
            "total_rev":       0.0,
            "profit":          0.0,
        }

    cr = _clamp(ar["conversions"] / ar["clicks"]) if ar["clicks"] > 0 else 0.0
    return {
        "content_id":      ar["content_id"],
        "niche":           ar["niche"],
        "page_id":         ar["page_id"],
        "account_id":      ar["account_id"],
        "clicks":          ar["clicks"],
        "conversions":     ar["conversions"],
        "conversion_rate": round(cr, 4),
        "attributed_rev":  round(ar["attributed_rev"], 4),
        "assist_rev":      round(ar["assist_rev"],     4),
        "total_rev":       round(ar["total_rev"],      4),
        "profit":          round(ar["total_rev"],      4),  # cost=0 default
    }


def get_attribution_log(last_n: int = 100) -> list[dict[str, Any]]:
    """Return the most recent N attribution flush entries (in-process log)."""
    return _ATTR_LOG[-last_n:]


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_attribution_state() -> None:
    """Clear all attribution state. For testing only."""
    _ATTR_LOG.clear()
    try:
        store = _get_store()
        store.clear()
    except Exception as exc:
        LOGGER.warning("attribution_engine reset_failed error=%s", exc)
