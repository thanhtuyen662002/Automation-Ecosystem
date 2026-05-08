"""
Session Orchestrator — Layer 3+4+5+6 unified entry point.

Flow:
    orchestrator.prepare(account_id, signals, profile, content_input=...)
        ├── StealthBrain.evaluate()              → Strategy       (what is SAFE)
        ├── MutationController.apply()           → fingerprint mutation
        ├── BehavioralBrain.build_session_plan() → SessionPlan    (how to BEHAVE)
        │       ↳ Strategy safety gates intent selection
        ├── ContentEngine.build_plan()           → ContentPlan    (what to produce)
        │       ↳ Skipped if HIGH risk or intent != UPLOAD
        └── MediaGenerator.render()              → MediaResult    (rendered assets)

    orchestrator.finalize(ctx, outcome_overrides)
        ├── BehavioralBrain.analyze_session()    → behavior_signals
        ├── media signals merged into outcome
        └── StealthBrain.record_outcome()        → update memory + risk_history

Design contracts:
  - Strategy is the ONE gate for content production. HIGH risk → no content.
  - ContentEngine is stateless per call; output stored in SessionContext.
  - MediaGenerator called only when intent == UPLOAD and risk < HIGH.
  - All new SessionResult fields default to empty strings — backward compat.

Usage:
    from core.session_orchestrator import get_session_orchestrator, SessionResult

    orch   = get_session_orchestrator()
    ctx    = orch.prepare(account_id, signals, profile,
                          content_input={"type":"product","source":"...","mode":"create"})
    # ... executor.run(ctx.plan) ...
    result = orch.finalize(ctx, outcome_overrides={"upload_success": True})
    print(result.caption, result.thumbnail)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from core.stealth_brain import get_stealth_brain, StealthBrain
from core.behavioral_brain import get_behavioral_brain, BehavioralBrain, SessionPlan
from core.mutation_controller import get_mutation_controller, MutationController, MutationResult
from core.content_engine import get_content_engine, ContentEngine, ContentPlan
from core.media_generator import get_media_generator, MediaGenerator, MediaResult

if TYPE_CHECKING:
    from core.identity_manager import IdentityProfile
    from core.mutation_controller import Strategy, RiskLevel
    from core.runtime_validator import RuntimeSignals

LOGGER = logging.getLogger("core.session_orchestrator")


# ── SessionContext ─────────────────────────────────────────────────────────────

@dataclass
class SessionContext:
    """
    Holds all objects produced during prepare() so finalize() can complete the loop.
    The executor receives this and populates plan.actual_duration / plan.abandoned_count.

    New fields (Layer 5/6):
      content_plan  — set when intent == UPLOAD and risk < HIGH; None otherwise.
      media_result  — set when content was rendered; None otherwise.
    """
    account_id:       str
    profile:          "IdentityProfile"
    signals:          "RuntimeSignals"
    strategy:         "Strategy"
    mutation_result:  MutationResult
    plan:             SessionPlan
    prepared_at:      float        = field(default_factory=time.time)
    content_plan:     ContentPlan  | None = None
    media_result:     MediaResult  | None = None


# ── SessionResult ─────────────────────────────────────────────────────────────

@dataclass
class SessionResult:
    """Summary returned by finalize(). Useful for FleetCoordinator health tracking."""
    account_id:       str
    intent:           str
    risk_level:       str
    mutation_type:    str
    estimated_secs:   float
    actual_secs:      float
    abandoned_count:  int
    upload_success:   bool
    captcha:          bool
    blocked:          bool
    # Layer 5/6 content fields — empty string when no content was produced
    video_path:       str   = ""
    thumbnail:        str   = ""   # path to first rendered image (cover frame)
    caption:          str   = ""   # hook text from ContentPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":      self.account_id,
            "intent":          self.intent,
            "risk_level":      self.risk_level,
            "mutation_type":   self.mutation_type,
            "estimated_secs":  self.estimated_secs,
            "actual_secs":     self.actual_secs,
            "abandoned_count": self.abandoned_count,
            "upload_success":  self.upload_success,
            "captcha":         self.captcha,
            "blocked":         self.blocked,
            "video_path":      self.video_path,
            "thumbnail":       self.thumbnail,
            "caption":         self.caption,
        }


# ── SessionOrchestrator ───────────────────────────────────────────────────────

class SessionOrchestrator:
    """
    Single wiring point for Layer 3 + Layer 4.

    Public API:
        prepare()   → SessionContext  (call before executor)
        finalize()  → SessionResult   (call after executor)

    Or convenience:
        run_sync()  → SessionResult   (for testing / non-async callers)
    """

    def __init__(
        self,
        stealth_brain:    StealthBrain     | None = None,
        behavioral_brain: BehavioralBrain  | None = None,
        mutation_ctrl:    MutationController | None = None,
        content_engine:   ContentEngine    | None = None,
        media_generator:  MediaGenerator   | None = None,
        media_output_dir: str = "output",
    ) -> None:
        self._stealth    = stealth_brain    or get_stealth_brain()
        self._behavioral = behavioral_brain or get_behavioral_brain()
        self._mc         = mutation_ctrl    or get_mutation_controller()
        self._content    = content_engine   or get_content_engine()
        self._media      = media_generator  or get_media_generator()
        self._media_dir  = media_output_dir

    # ── Core API ──────────────────────────────────────────────────────────────

    def prepare(
        self,
        account_id:    str,
        signals:       "RuntimeSignals",
        profile:       "IdentityProfile",
        session_index: int | None = None,
        content_input: dict[str, Any] | None = None,
        render_video:  bool = False,
    ) -> SessionContext:
        """
        Evaluate risk → mutation → behavior plan → content plan → media render.

        Args:
            account_id:    Account identifier.
            signals:       RuntimeSignals from the fingerprint validator.
            profile:       IdentityProfile (will be mutated if needed).
            session_index: Override session counter for BehavioralBrain (optional).
            content_input: Dict passed to ContentEngine.build_plan().
                           Keys: type, source, mode  (account_id injected automatically).
                           If None, ContentEngine is skipped.
            render_video:  If True, VideoRenderer runs (slow — for production only).
                           Default False: only images are rendered.

        Returns:
            SessionContext with strategy, mutation_result, plan,
            and optionally content_plan + media_result.
        """
        from core.mutation_controller import RiskLevel
        from core.behavioral_brain import SessionIntent

        # 1. Layer 3: risk evaluation
        strategy = self._stealth.evaluate(account_id, signals, profile)

        # 2. Fingerprint mutation (if strategy says so)
        mutation_result = self._mc.apply(profile, strategy)

        # 3. Layer 4: build behavior plan, gated by Layer 3 strategy
        mem  = self._stealth.get_memory(account_id)
        plan = self._behavioral.build_session_plan(
            account_id     = account_id,
            profile        = profile,
            strategy       = strategy,
            signals        = signals,
            stealth_memory = mem,
            session_index  = session_index,
        )

        # 4. Layer 5: content plan — only when safe AND upload-intent
        content_plan: ContentPlan | None = None
        media_result: MediaResult | None = None

        content_allowed = (
            strategy.risk_level != RiskLevel.HIGH
            and plan.intent == SessionIntent.UPLOAD
            and content_input is not None
        )

        if content_allowed:
            try:
                ci = {**content_input, "account_id": account_id}
                content_plan = self._content.build_plan(ci, profile=profile)

                # 5. Layer 6: media render (images always; video only if requested)
                out_dir = f"{self._media_dir}/{account_id}"
                media_result = self._media.render(
                    content_plan,
                    output_dir   = out_dir,
                    render_video = render_video,
                )
                LOGGER.info("content_produced", extra={
                    "account_id":  account_id,
                    "template_id": content_plan.template_id,
                    "images":      len(media_result.images),
                    "video_path":  media_result.video_path,
                })
            except Exception as exc:  # never let content failure break the session
                LOGGER.error("content_engine_error", extra={
                    "account_id": account_id,
                    "error":      str(exc),
                    "error_type": type(exc).__name__,
                })
        else:
            reason = (
                "high_risk"           if strategy.risk_level == RiskLevel.HIGH else
                "intent_not_upload"   if plan.intent != SessionIntent.UPLOAD else
                "no_content_input"
            )
            LOGGER.debug("content_skipped", extra={"account_id": account_id, "reason": reason})

        LOGGER.info("session_prepared", extra={
            "account_id":       account_id,
            "risk_level":       strategy.risk_level.value,
            "mutation_type":    mutation_result.mutation_type,
            "intent":           plan.intent.value,
            "steps":            len(plan.steps),
            "estimated_secs":   plan.estimated_duration,
            "content_produced": content_plan is not None,
        })

        return SessionContext(
            account_id      = account_id,
            profile         = profile,
            signals         = signals,
            strategy        = strategy,
            mutation_result = mutation_result,
            plan            = plan,
            content_plan    = content_plan,
            media_result    = media_result,
        )

    def finalize(
        self,
        ctx:               SessionContext,
        outcome_overrides: dict[str, Any] | None = None,
    ) -> SessionResult:
        """
        Analyze session behavior, merge media signals, record outcome to StealthBrain.

        Call this AFTER the executor has set:
            ctx.plan.actual_duration
            ctx.plan.abandoned_count

        Args:
            ctx:               SessionContext from prepare().
            outcome_overrides: Additional outcome fields (upload_success, captcha, blocked, …).

        Returns:
            SessionResult with behavioral + media metadata.
        """
        plan    = ctx.plan
        profile = ctx.profile

        # Layer 4 post-session analysis → behavior signals
        behavior_signals = self._behavioral.analyze_session(plan)
        behavior_signals["estimated_duration"] = plan.estimated_duration

        # Layer 5/6 media signals → injected into outcome so StealthBrain logs them
        media_signals: dict[str, Any] = {}
        video_path = ""
        thumbnail  = ""
        caption    = ""

        if ctx.media_result is not None:
            mr = ctx.media_result
            video_path = mr.video_path or ""
            thumbnail  = mr.images[0] if mr.images else ""
            media_signals["media_produced"]  = True
            media_signals["media_image_count"] = len(mr.images)
            media_signals["media_has_video"]   = bool(video_path)

        if ctx.content_plan is not None:
            cp = ctx.content_plan
            caption = next(
                (s.text for s in cp.script if s.role == "hook"),
                cp.script[0].text if cp.script else "",
            )
            media_signals["content_template"] = cp.template_id
            media_signals["content_duration"]  = cp.duration

        # Merge: behavior < media < caller overrides  (caller wins)
        outcome = {**behavior_signals, **media_signals, **(outcome_overrides or {})}

        # Layer 3: record outcome (updates memory + risk_history)
        self._stealth.record_outcome(ctx.account_id, outcome, profile)

        result = SessionResult(
            account_id      = ctx.account_id,
            intent          = plan.intent.value,
            risk_level      = ctx.strategy.risk_level.value,
            mutation_type   = ctx.mutation_result.mutation_type,
            estimated_secs  = plan.estimated_duration,
            actual_secs     = plan.actual_duration,
            abandoned_count = plan.abandoned_count,
            upload_success  = bool(outcome.get("upload_success", False)),
            captcha         = bool(outcome.get("captcha",         False)),
            blocked         = bool(outcome.get("blocked",         False)),
            video_path      = video_path,
            thumbnail       = thumbnail,
            caption         = caption,
        )

        LOGGER.info("session_finalized", extra={
            "account_id":       ctx.account_id,
            "intent":           result.intent,
            "risk_level":       result.risk_level,
            "actual_secs":      result.actual_secs,
            "upload_success":   result.upload_success,
            "blocked":          result.blocked,
            "content_produced": ctx.content_plan is not None,
            "video_path":       result.video_path,
        })

        return result

    def run_sync(
        self,
        account_id:        str,
        signals:           "RuntimeSignals",
        profile:           "IdentityProfile",
        outcome_overrides: dict[str, Any] | None = None,
        session_index:     int | None = None,
        content_input:     dict[str, Any] | None = None,
        render_video:      bool = False,
    ) -> SessionResult:
        """
        Convenience wrapper: prepare + simulate instant execution + finalize.
        Useful for testing and non-async contexts.

        NOTE: This does NOT pause for step durations.
              It marks actual_duration = estimated_duration.

        Args:
            content_input: Forwarded to prepare(); triggers ContentEngine + MediaGenerator.
            render_video:  Forwarded to prepare(); set True to also render MP4.
        """
        ctx = self.prepare(
            account_id,
            signals,
            profile,
            session_index  = session_index,
            content_input  = content_input,
            render_video   = render_video,
        )
        ctx.plan.actual_duration = ctx.plan.estimated_duration
        ctx.plan.abandoned_count = 0
        return self.finalize(ctx, outcome_overrides=outcome_overrides)

    # ── Persistence helpers ───────────────────────────────────────────────────

    def snapshot_all(self) -> dict[str, Any]:
        return {
            "stealth":    self._stealth.snapshot_all(),
            "behavioral": self._behavioral.snapshot_all(),
        }

    def load_all(self, data: dict[str, Any]) -> None:
        if "stealth" in data:
            self._stealth.load_all(data["stealth"])
        if "behavioral" in data:
            self._behavioral.load_all(data["behavioral"])


# ── Singleton ─────────────────────────────────────────────────────────────────

_SESSION_ORCHESTRATOR: SessionOrchestrator | None = None


def get_session_orchestrator(
    media_output_dir: str = "output",
    render_video: bool = False,
) -> SessionOrchestrator:
    """Return the process-level SessionOrchestrator singleton.

    Args:
        media_output_dir: Root directory for rendered media files.
        render_video:     Not stored — pass per-call to run_sync/prepare instead.
    """
    global _SESSION_ORCHESTRATOR
    if _SESSION_ORCHESTRATOR is None:
        _SESSION_ORCHESTRATOR = SessionOrchestrator(media_output_dir=media_output_dir)
    return _SESSION_ORCHESTRATOR
