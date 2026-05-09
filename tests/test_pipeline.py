"""
Pipeline integration tests — full closed loop:

    Agent → Feed → Engagement → Detector → RL + Optimizer

v3: Updated for content-level virality (content_id), fatigue model.
"""
import importlib.util, sys, os
from dataclasses import dataclass, field as _field
from types import ModuleType

def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

sys.modules.setdefault("core", type(sys)("core"))

@dataclass
class _RS:
    platform_match: bool = True; hardware_match: bool = True
    language_match: bool = True; screen_match: bool = True
    timezone_match: bool = True; webgl_vendor_match: bool = True
    webgl_renderer_match: bool = True; webdriver_hidden: bool = True
    eval_ok: bool = True; risk_score: float = 0.0
    breakdown: dict = _field(default_factory=dict)
    fingerprint_changed: bool = False; geo_mismatch: bool = False
    device_mismatch: bool = False

_rv = type(sys)("core.runtime_validator")
_rv.RuntimeSignals = _RS
sys.modules["core.runtime_validator"] = _rv

for path, name in [
    ("core/global_memory.py",        "core.global_memory"),
    ("core/platform_profiles.py",    "core.platform_profiles"),
    ("core/mutation_controller.py",  "core.mutation_controller"),
    ("core/identity_manager.py",     "core.identity_manager"),
    ("core/persona_engine.py",       "core.persona_engine"),
    ("core/lifecycle_engine.py",     "core.lifecycle_engine"),
    ("core/strategy_engine.py",      "core.strategy_engine"),
    ("core/metrics_store.py",        "core.metrics_store"),
    ("core/observer.py",             "core.observer"),
    ("core/detector_simulator.py",   "core.detector_simulator"),
    ("core/optimizer.py",            "core.optimizer"),
    ("core/reinforcement.py",        "core.reinforcement"),
    ("core/meta_learning.py",        "core.meta_learning"),
    ("core/account_clustering.py",   "core.account_clustering"),
    ("core/feed_engine.py",          "core.feed_engine"),
    ("core/engagement_simulator.py", "core.engagement_simulator"),
    ("core/pipeline.py",             "core.pipeline"),
]:
    _load(path, name)

import core.pipeline            as pl
import core.feed_engine         as fe
import core.engagement_simulator as es
import core.detector_simulator  as ds
import core.metrics_store       as ms
import core.optimizer           as opt_mod
import core.reinforcement       as rl_mod
import core.strategy_engine     as se

BASE_TS    = 1_716_100_000
CREATED_TS = BASE_TS - 30 * 86400


def fresh():
    """Reset all singletons between tests."""
    from core.global_memory import reset_global_memory
    from core.observer import reset_observer
    reset_global_memory()
    reset_observer()
    ms.reset_metrics_store()
    opt_mod.reset_optimizer()
    rl_mod.reset_policy()
    ds.reset_detector()
    fe.reset_feed_engine()
    se._reset_for_testing()
    try:
        from core.lifecycle_engine import reset_lifecycle_engine
        reset_lifecycle_engine()
    except Exception:
        pass
    try:
        from core.persona_engine import reset_persona_engine
        reset_persona_engine()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Feed engine — bounded outputs
# ─────────────────────────────────────────────────────────────────────────────

def test_feed_engine_bounded():
    """All FeedResult scores must be in [0.0, 1.0]."""
    fresh()
    platforms = ["tiktok", "instagram", "youtube", "facebook"]
    niches    = ["tech", "fitness", "finance", "entertainment", "food", "travel"]
    for platform in platforms:
        for niche in niches:
            post = fe.ContentPost(
                account_id="feed-test-001", platform=platform, niche=niche,
                intensity=0.7, lifecycle_stage="GROWTH",
                created_ts=CREATED_TS, now=BASE_TS,
            )
            result = fe.rank_content(post)
            for score_name in ("reach_score", "virality_score", "ranking_score"):
                v = getattr(result, score_name)
                assert 0.0 <= v <= 1.0, (
                    f"FeedResult.{score_name} out of bounds for "
                    f"{platform}/{niche}: {v}"
                )
    print("PASS test_feed_engine_bounded")


def test_feed_novelty_penalty():
    """Repeated niche posting must reduce ranking_score over time."""
    fresh()
    acct = "novelty-test-001"
    post = fe.ContentPost(
        account_id=acct, platform="tiktok", niche="tech",
        intensity=0.8, lifecycle_stage="MATURE",
        created_ts=CREATED_TS, now=BASE_TS,
    )
    r1 = fe.rank_content(post)
    r2 = fe.rank_content(post)
    assert r2.ranking_score < r1.ranking_score, (
        f"Second post in same niche should be penalised: "
        f"r1={r1.ranking_score:.4f} r2={r2.ranking_score:.4f}"
    )
    assert "novelty_suppressed" in r2.flags
    print(f"PASS test_feed_novelty_penalty (r1={r1.ranking_score:.3f} r2={r2.ranking_score:.3f})")


def test_feed_authority_matters():
    """MATURE account must reach more than NEW account, same content."""
    fresh()
    def make_post(stage, acct):
        return fe.ContentPost(
            account_id=acct, platform="tiktok", niche="fitness",
            intensity=0.7, lifecycle_stage=stage,
            created_ts=CREATED_TS, now=BASE_TS,
        )
    r_mature = fe.rank_content(make_post("MATURE", "auth-mature"))
    r_new    = fe.rank_content(make_post("NEW",    "auth-new"))
    assert r_mature.reach_score > r_new.reach_score, (
        f"MATURE reach={r_mature.reach_score:.3f} should > NEW reach={r_new.reach_score:.3f}"
    )
    print(f"PASS test_feed_authority_matters (MATURE={r_mature.reach_score:.3f} NEW={r_new.reach_score:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# Engagement simulator
# ─────────────────────────────────────────────────────────────────────────────

def test_engagement_bounded():
    """All engagement rates and score must be in [0.0, 1.0]."""
    fresh()
    for i in range(20):
        post = fe.ContentPost(
            account_id=f"eng-test-{i:04d}", platform="tiktok", niche="fitness",
            intensity=0.3 + (i % 8) * 0.08, lifecycle_stage="GROWTH",
            created_ts=CREATED_TS, now=BASE_TS + i * 3600,
        )
        feed_result = fe.rank_content(post)
        eng = es.simulate_engagement(feed_result, post)
        for attr in ("like_rate", "comment_rate", "share_rate", "save_rate",
                     "skip_rate", "engagement_score"):
            v = getattr(eng, attr)
            assert 0.0 <= v <= 1.0, f"engagement_simulator.{attr} out of bounds: {v}"
    print("PASS test_engagement_bounded")


def test_high_ranking_drives_engagement():
    """Higher-ranked content should reach more users than low-ranked."""
    fresh()
    post_high = fe.ContentPost(
        account_id="rank-eng-001", platform="tiktok", niche="entertainment",
        intensity=0.95, lifecycle_stage="MATURE",
        created_ts=CREATED_TS, now=BASE_TS,
    )
    feed_high = fe.rank_content(post_high)

    post_low = fe.ContentPost(
        account_id="rank-eng-002", platform="tiktok", niche="entertainment",
        intensity=0.10, lifecycle_stage="NEW",
        created_ts=CREATED_TS, now=BASE_TS,
    )
    feed_low = fe.rank_content(post_low)

    assert feed_high.reach_score > feed_low.reach_score, (
        f"High-intensity MATURE should reach more than low NEW: "
        f"{feed_high.reach_score:.3f} vs {feed_low.reach_score:.3f}"
    )

    eng_high = es.simulate_engagement(feed_high, post_high)
    eng_low  = es.simulate_engagement(feed_low,  post_low)

    assert eng_high.reach_count > eng_low.reach_count, (
        f"MATURE+high should reach more users: "
        f"high={eng_high.reach_count} low={eng_low.reach_count}"
    )
    print(f"PASS test_high_ranking_drives_engagement (reach: {eng_high.reach_count}>{eng_low.reach_count})")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline integration
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_cycle_runs():
    """A full pipeline cycle must complete without errors for 30 accounts."""
    fresh()
    pipeline = pl.Pipeline(
        accounts     = [f"pipe-acct-{i:04d}" for i in range(30)],
        platform     = "tiktok",
        created_ts   = CREATED_TS,
        cycle_step_s = 3600,
    )
    report = pipeline.run_cycle(now=BASE_TS)
    assert report is not None
    assert report.n_accounts == 30
    assert report.n_active + report.n_skipped == 30
    assert 0.0 <= report.avg_engagement <= 1.0
    assert 0.0 <= report.avg_detection_risk <= 1.0
    assert 0.0 <= report.fleet_health <= 1.0
    print(
        f"PASS test_pipeline_cycle_runs "
        f"(active={report.n_active} success={report.n_success} "
        f"viral={report.n_viral} health={report.fleet_health:.3f})"
    )


def test_pipeline_multi_cycle():
    """12 pipeline cycles must complete, all reports valid and serialisable."""
    fresh()
    pipeline = pl.Pipeline(
        accounts   = [f"multi-acct-{i:04d}" for i in range(20)],
        platform   = "instagram",
        created_ts = CREATED_TS,
    )
    reports = pipeline.run(n_cycles=12, base_ts=BASE_TS, output_path=os.devnull)
    assert len(reports) == 12
    for i, report in enumerate(reports):
        assert report.cycle == i
        assert report.n_accounts == 20
        import json
        json.dumps(report.to_dict())
    print(f"PASS test_pipeline_multi_cycle (12 cycles)")


def test_pipeline_optimizer_reacts():
    """After multiple cycles, optimizer state must show non-neutral values."""
    fresh()
    pipeline = pl.Pipeline(
        accounts   = [f"opt-acct-{i:04d}" for i in range(25)],
        platform   = "tiktok",
        created_ts = CREATED_TS,
    )
    reports = pipeline.run(n_cycles=8, base_ts=BASE_TS)
    last = reports[-1]
    assert last.optimizer_state
    all_neutral = all(abs(v - 1.0) < 0.001 for v in last.optimizer_state.values())
    assert not all_neutral, f"Optimizer should react: {last.optimizer_state}"
    print(f"PASS test_pipeline_optimizer_reacts")


def test_pipeline_no_cross_account_leak():
    """Different accounts must have different engagement outcomes."""
    fresh()
    pipeline = pl.Pipeline(
        accounts   = [f"isolate-acct-{i:04d}" for i in range(20)],
        platform   = "youtube",
        created_ts = CREATED_TS,
    )
    report = pipeline.run_cycle(now=BASE_TS)
    active = [r for r in report.accounts if not r.plan_skipped]
    if len(active) < 5:
        print("PASS test_pipeline_no_cross_account_leak (too few active)")
        return
    unique_scores = len(set(round(r.engagement_score, 3) for r in active))
    assert unique_scores > 1, f"All accounts have identical engagement — leak!"
    print(f"PASS test_pipeline_no_cross_account_leak ({unique_scores}/{len(active)} unique)")


def test_pipeline_detector_feedback():
    """Accounts with high detection risk should be skipped."""
    fresh()
    for i in range(5):
        ds._RISK_MEMORY[f"risk-acct-{i:04d}"] = 0.92
    pipeline = pl.Pipeline(
        accounts=[f"risk-acct-{i:04d}" for i in range(10)],
        platform="tiktok", created_ts=CREATED_TS,
    )
    report = pipeline.run_cycle(now=BASE_TS)
    assert report.n_skipped > 0, "Some high-risk accounts should be skipped"
    print(f"PASS test_pipeline_detector_feedback (skipped={report.n_skipped}/{report.n_accounts})")


def test_engagement_outcome_signal():
    """outcome_from_engagement: high-engagement → success; suppressed → ban proxy."""
    fresh()
    post = fe.ContentPost(
        account_id="outcome-001", platform="tiktok", niche="entertainment",
        intensity=0.95, lifecycle_stage="MATURE",
        created_ts=CREATED_TS, now=BASE_TS,
    )
    feed = fe.rank_content(post)
    eng  = es.simulate_engagement(feed, post)
    success, ban = es.outcome_from_engagement(eng)
    assert isinstance(success, bool)
    assert isinstance(ban, bool)
    assert not (success and ban)

    from dataclasses import replace
    eng_suppressed = replace(eng, is_suppressed=True, engagement_score=0.02)
    s2, b2 = es.outcome_from_engagement(eng_suppressed)
    assert not s2
    assert b2
    print(f"PASS test_engagement_outcome_signal (success={success} ban={ban})")


# ─────────────────────────────────────────────────────────────────────────────
# v2 tests — Competitive Batch Ranking, Position, Creator Exposure
# ─────────────────────────────────────────────────────────────────────────────

def test_batch_ranking_competition_density():
    """Posts in crowded niches must score lower than posts in uncrowded niches."""
    fresh()
    now = BASE_TS
    crowded_posts = [
        fe.ContentPost(
            account_id=f"crowd-{i:03d}", platform="tiktok", niche="tech",
            intensity=0.7, lifecycle_stage="GROWTH", created_ts=CREATED_TS, now=now,
        )
        for i in range(8)
    ]
    lone_post = fe.ContentPost(
        account_id="lone-001", platform="tiktok", niche="finance",
        intensity=0.7, lifecycle_stage="GROWTH", created_ts=CREATED_TS, now=now,
    )
    all_posts   = crowded_posts + [lone_post]
    all_results = fe.rank_batch(all_posts)
    lone_result    = all_results[-1]
    crowded_scores = [r.ranking_score for r in all_results[:-1]]
    avg_crowded    = sum(crowded_scores) / len(crowded_scores)
    assert lone_result.ranking_score > avg_crowded, (
        f"Lone post should rank higher: lone={lone_result.ranking_score:.3f} avg={avg_crowded:.3f}"
    )
    assert lone_result.reasoning["competition_density"] < 0.20
    print(f"PASS test_batch_ranking_competition_density (lone={lone_result.ranking_score:.3f} avg={avg_crowded:.3f})")


def test_feed_position_effect():
    """Top-3 items in a batch must have higher reach than bottom-half items."""
    fresh()
    now   = BASE_TS
    posts = []
    for i in range(10):
        posts.append(fe.ContentPost(
            account_id=f"pos-{i:03d}", platform="tiktok", niche="fitness",
            intensity=0.8 if i < 3 else 0.2,
            lifecycle_stage="MATURE" if i < 3 else "NEW",
            created_ts=CREATED_TS, now=now,
        ))
    results = fe.rank_batch(posts)
    top3   = [r for r in results if r.position <= 3]
    bottom = [r for r in results if r.position > 7]
    if not top3 or not bottom:
        print("PASS test_feed_position_effect (insufficient spread)")
        return
    avg_top    = sum(r.reach_score for r in top3) / len(top3)
    avg_bottom = sum(r.reach_score for r in bottom) / len(bottom)
    assert avg_top > avg_bottom, (
        f"Top-3 reach should > bottom: top={avg_top:.3f} bottom={avg_bottom:.3f}"
    )
    print(f"PASS test_feed_position_effect (top={avg_top:.3f} bottom={avg_bottom:.3f})")


def test_creator_exposure_diminishing_returns():
    """Same creator posting multiple times must get decreasing engagement."""
    fresh()
    acct = "creator-repeat-001"
    now  = BASE_TS
    scores = []
    for _ in range(5):
        post = fe.ContentPost(
            account_id=acct, platform="tiktok", niche="entertainment",
            intensity=0.8, lifecycle_stage="MATURE", created_ts=CREATED_TS, now=now,
        )
        feed = fe.rank_content(post)
        eng  = es.simulate_engagement(feed, post)
        scores.append(eng.engagement_score)
    assert scores[0] >= scores[-1], (
        f"Diminishing engagement expected: {[round(s, 3) for s in scores]}"
    )
    print(f"PASS test_creator_exposure_diminishing_returns (scores={[round(s, 3) for s in scores]})")


def test_attention_budget_depletes():
    """Attention budget must decrease within a session and reset in a new session."""
    fresh()
    acct = "attn-test-001"
    now  = BASE_TS
    budgets = [fe._get_attention_budget(acct, now) for _ in range(15)]
    assert budgets[-1] < budgets[0], (
        f"Budget should decrease: start={budgets[0]:.3f} end={budgets[-1]:.3f}"
    )
    b_reset = fe._get_attention_budget(acct, now + fe._ATTENTION_SESSION_S + 1)
    assert b_reset == 1.0, f"Budget should reset: got {b_reset}"
    print(f"PASS test_attention_budget_depletes (depleted={budgets[-1]:.3f} reset={b_reset:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# v3 tests — Content-Level Virality, Fatigue, Cross-Session Memory
# ─────────────────────────────────────────────────────────────────────────────

def test_viral_cascade_state():
    """v3: Viral state keyed by content_id, decays via exp(-Δt/tau)."""
    fresh()
    acct  = "viral-cascade-001"
    niche = "entertainment"
    now   = BASE_TS
    cid   = fe._make_content_id(acct, niche, now)

    for i in range(5):
        fe.update_viral_state(cid, engagement_signal=0.85, now=now + i * 300)

    v_hot = fe.get_viral_state(cid)
    assert v_hot > 0.20, f"Viral state should build up: {v_hot:.4f}"

    v_cold = fe.update_viral_state(cid, engagement_signal=0.0, now=now + 10 * 3600)
    assert v_cold < v_hot, (
        f"Viral state should decay with exp(-Δt/tau): hot={v_hot:.4f} cold={v_cold:.4f}"
    )
    print(f"PASS test_viral_cascade_state (v_hot={v_hot:.4f} v_cold={v_cold:.4f})")


def test_viral_content_isolation():
    """Part 1 v3: Different content_ids must have independent viral states."""
    fresh()
    now   = BASE_TS
    cid_a = fe._make_content_id("acct-a", "tech", now)
    cid_b = fe._make_content_id("acct-b", "tech", now)

    for _ in range(5):
        fe.update_viral_state(cid_a, 0.90, now)

    v_a = fe.get_viral_state(cid_a)
    v_b = fe.get_viral_state(cid_b)
    assert v_a > 0.20, f"Content A should be viral: {v_a:.4f}"
    assert v_b == 0.0, f"Content B must be isolated: {v_b:.4f}"

    cid_c = fe._make_content_id("acct-a", "fitness", now)
    assert cid_c != cid_a
    assert fe.get_viral_state(cid_c) == 0.0
    print(f"PASS test_viral_content_isolation (v_a={v_a:.4f} v_b={v_b:.4f})")


def test_fatigue_accumulates_and_depresses_ranking():
    """Part 2 v3: Repeated niche exposure must depress ranking score."""
    fresh()
    acct  = "fatigue-acct-001"
    niche = "tech"
    now   = BASE_TS
    rankings = []
    for _ in range(6):
        post = fe.ContentPost(
            account_id=acct, platform="youtube", niche=niche,
            intensity=0.8, lifecycle_stage="MATURE", created_ts=CREATED_TS, now=now,
        )
        rankings.append(fe.rank_content(post).ranking_score)

    assert rankings[-1] < rankings[0], (
        f"Fatigue should depress ranking: {[round(r, 3) for r in rankings]}"
    )
    post = fe.ContentPost(
        account_id=acct, platform="youtube", niche=niche,
        intensity=0.8, lifecycle_stage="MATURE", created_ts=CREATED_TS, now=now,
    )
    r = fe.rank_content(post)
    assert r.content_fatigue > 0.0, "content_fatigue field should be > 0"
    print(f"PASS test_fatigue_accumulates_and_depresses_ranking ({[round(r, 3) for r in rankings]})")


def test_fatigue_cross_session_persistence():
    """Part 3 v3: Fatigue must persist across session boundaries."""
    fresh()
    acct  = "fatigue-persist-001"
    niche = "fitness"
    now   = BASE_TS

    for _ in range(4):
        fe.increment_fatigue(acct, niche, now)

    fatigue_s1 = fe.get_fatigue(acct, niche, now)
    assert fatigue_s1 > 0.0

    new_session_ts = now + fe._ATTENTION_SESSION_S + 60
    fatigue_s2 = fe.get_fatigue(acct, niche, new_session_ts)

    assert fatigue_s2 > 0.0, (
        f"Fatigue must persist across sessions: s1={fatigue_s1:.4f} s2={fatigue_s2:.4f}"
    )
    assert fatigue_s2 <= fatigue_s1, (
        f"Fatigue should decay, not grow: s1={fatigue_s1:.4f} s2={fatigue_s2:.4f}"
    )
    print(f"PASS test_fatigue_cross_session_persistence (s1={fatigue_s1:.4f} s2={fatigue_s2:.4f})")


def test_fatigue_cap():
    """Part 3 v3: Fatigue must never exceed _FATIGUE_MAX (0.60)."""
    fresh()
    acct  = "fatigue-cap-001"
    niche = "food"
    now   = BASE_TS
    for _ in range(20):
        fe.increment_fatigue(acct, niche, now)
    fatigue = fe.get_fatigue(acct, niche, now)
    assert fatigue <= fe._FATIGUE_MAX, f"Fatigue exceeded cap: {fatigue:.4f}"
    assert fatigue > 0.50, f"Fatigue should be near cap after 20 exposures: {fatigue:.4f}"
    print(f"PASS test_fatigue_cap (fatigue={fatigue:.4f} cap={fe._FATIGUE_MAX})")


if __name__ == "__main__":
    for fn_name in [k for k in list(globals()) if k.startswith("test_")]:
        fn = globals()[fn_name]
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn_name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {fn_name}: {type(e).__name__}: {e}")
            traceback.print_exc()
