"""
Content Engine — Layer 6: Content Plan Generator.

Modes:
    remake  — rewrite existing content with new angle/hook
    reup    — re-upload existing content with minor refresh
    create  — original content from scratch

Input schema:
    {"type": "product|video", "source": "...", "account_id": "..."}

Output: ContentPlan
    {"script": list[dict], "visual_plan": list[dict],
     "template_id": str, "duration": float}

Design contracts:
  - 100% deterministic per account_id (seed-based, no random module)
  - IdentityProfile drives style hints (locale, device_type, timezone)
  - At least 3 distinct script patterns per mode
  - No media rendering — plan only
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity_manager import IdentityProfile

LOGGER = logging.getLogger("core.content_engine")


# ── PRNG helpers ──────────────────────────────────────────────────────────────

def _cseed(account_id: str, slot: int) -> float:
    """Deterministic float [0,1) from account_id + slot."""
    h = hashlib.sha256(f"ce:{account_id}:{slot}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _cpick(account_id: str, slot: int, pool: list) -> Any:
    return pool[int(_cseed(account_id, slot) * len(pool))]


def _cint(account_id: str, slot: int, lo: int, hi: int) -> int:
    return lo + int(_cseed(account_id, slot) * (hi - lo + 1))


def _cfloat(account_id: str, slot: int, lo: float, hi: float) -> float:
    return round(lo + _cseed(account_id, slot) * (hi - lo), 2)


# ── Enums ─────────────────────────────────────────────────────────────────────

class ContentMode(str, Enum):
    REMAKE = "remake"
    REUP   = "reup"
    CREATE = "create"


class ContentType(str, Enum):
    PRODUCT = "product"
    VIDEO   = "video"


# ── Script patterns (3 per mode × 2 types = rich variation) ──────────────────
# Each pattern defines segment labels and duration weights.
# Actual text is seeded-per-account so two accounts differ.

_SCRIPT_PATTERNS: dict[str, list[dict]] = {
    # Pattern key = f"{mode}:{type}:{pattern_index}"
    # Each entry: list of segment defs {role, label, weight}
    "hook_problem_cta": [
        {"role": "hook",   "label": "attention_grab",   "weight": 0.20},
        {"role": "body",   "label": "problem_statement", "weight": 0.30},
        {"role": "body",   "label": "solution_reveal",   "weight": 0.35},
        {"role": "cta",    "label": "call_to_action",    "weight": 0.15},
    ],
    "hook_story_cta": [
        {"role": "hook",   "label": "story_open",        "weight": 0.15},
        {"role": "body",   "label": "story_build",       "weight": 0.25},
        {"role": "body",   "label": "story_climax",      "weight": 0.30},
        {"role": "body",   "label": "product_bridge",    "weight": 0.20},
        {"role": "cta",    "label": "call_to_action",    "weight": 0.10},
    ],
    "hook_listicle_cta": [
        {"role": "hook",   "label": "listicle_tease",    "weight": 0.15},
        {"role": "body",   "label": "point_1",           "weight": 0.20},
        {"role": "body",   "label": "point_2",           "weight": 0.20},
        {"role": "body",   "label": "point_3",           "weight": 0.25},
        {"role": "cta",    "label": "call_to_action",    "weight": 0.20},
    ],
    "hook_comparison_cta": [
        {"role": "hook",   "label": "versus_open",       "weight": 0.15},
        {"role": "body",   "label": "before_state",      "weight": 0.25},
        {"role": "body",   "label": "after_state",       "weight": 0.30},
        {"role": "body",   "label": "differentiator",    "weight": 0.20},
        {"role": "cta",    "label": "call_to_action",    "weight": 0.10},
    ],
    "hook_demo_cta": [
        {"role": "hook",   "label": "demo_hook",         "weight": 0.10},
        {"role": "body",   "label": "feature_demo_1",    "weight": 0.25},
        {"role": "body",   "label": "feature_demo_2",    "weight": 0.25},
        {"role": "body",   "label": "benefit_summary",   "weight": 0.25},
        {"role": "cta",    "label": "call_to_action",    "weight": 0.15},
    ],
    "hook_transformation_cta": [
        {"role": "hook",   "label": "transformation_tease", "weight": 0.15},
        {"role": "body",   "label": "starting_point",       "weight": 0.20},
        {"role": "body",   "label": "journey",              "weight": 0.30},
        {"role": "body",   "label": "result_reveal",        "weight": 0.20},
        {"role": "cta",    "label": "call_to_action",       "weight": 0.15},
    ],
}

# Mode → 3 pattern keys (ordered); account seed picks which one
_MODE_PATTERNS: dict[ContentMode, list[str]] = {
    ContentMode.REMAKE: ["hook_story_cta",       "hook_comparison_cta",    "hook_transformation_cta"],
    ContentMode.REUP:   ["hook_problem_cta",      "hook_demo_cta",          "hook_listicle_cta"],
    ContentMode.CREATE: ["hook_listicle_cta",     "hook_story_cta",         "hook_comparison_cta"],
}

# ── Hook copy variants (seeded selection) ─────────────────────────────────────

_HOOK_VARIANTS: dict[str, list[str]] = {
    "attention_grab":       ["Stop scrolling — you need to see this",
                             "I can't believe this actually works",
                             "Everyone is sleeping on this"],
    "story_open":           ["Let me tell you what happened to me last week",
                             "I almost quit until I tried this",
                             "This changed everything for me"],
    "listicle_tease":       ["3 things nobody tells you about this",
                             "Top 5 reasons you're doing it wrong",
                             "Here's what I found after 30 days"],
    "versus_open":          ["Old way vs new way — huge difference",
                             "I compared 5 options so you don't have to",
                             "Before vs after — this is insane"],
    "demo_hook":            ["Watch this in the next 30 seconds",
                             "Let me show you exactly how it works",
                             "Live demo — no editing, no tricks"],
    "transformation_tease": ["I never thought I'd see results this fast",
                             "From zero to this in just 2 weeks",
                             "My transformation story — honest review"],
}

_CTA_VARIANTS: list[str] = [
    "Link in bio — grab yours before it's gone",
    "Comment 'INFO' and I'll DM you the details",
    "Follow for more tips like this",
    "Save this post — you'll thank me later",
    "DM me 'YES' if you want to know more",
]

_BODY_FILLERS: dict[str, list[str]] = {
    "problem_statement":    ["Most people struggle with exactly this",
                             "This is the #1 mistake everyone makes",
                             "You've probably felt this frustration too"],
    "solution_reveal":      ["Here's the exact fix I found",
                             "This one change made all the difference",
                             "The solution is simpler than you think"],
    "story_build":          ["It started when I noticed something strange",
                             "I kept trying but nothing seemed to work",
                             "Then someone showed me a completely different approach"],
    "story_climax":         ["That's when everything clicked",
                             "The results surprised even me",
                             "I couldn't believe the difference"],
    "product_bridge":       ["That's exactly why this product exists",
                             "This tool solves that exact problem",
                             "And this is what finally worked for me"],
    "point_1":              ["First — most people skip this step",
                             "Number one — and this is huge",
                             "Start with this — it changes everything"],
    "point_2":              ["Second — this one is counterintuitive",
                             "Here's the part nobody talks about",
                             "This is where most people go wrong"],
    "point_3":              ["And finally — this is the game changer",
                             "Last one — and it's the most important",
                             "This alone made the biggest difference"],
    "before_state":         ["Before I found this, I was struggling every day",
                             "The old way was slow, frustrating, and expensive",
                             "Nothing seemed to work no matter what I tried"],
    "after_state":          ["Now everything flows effortlessly",
                             "The results are completely different",
                             "I wish I had found this sooner"],
    "differentiator":       ["What makes this different is the approach",
                             "Unlike everything else I tried, this actually delivers",
                             "The key difference is how it works behind the scenes"],
    "feature_demo_1":       ["First feature — watch how smooth this is",
                             "Step one is incredibly simple",
                             "Here's what it looks like in action"],
    "feature_demo_2":       ["Second feature — this is the one people love",
                             "And now watch what happens when I do this",
                             "This part always gets a reaction"],
    "benefit_summary":      ["So in summary — faster, easier, better results",
                             "These three things together are what make it work",
                             "That's why thousands of people switched to this"],
    "starting_point":       ["Where I started was pretty rough honestly",
                             "My baseline was not great — I'll be honest",
                             "Here's what day one actually looked like"],
    "journey":              ["Week one was all about building the habit",
                             "The process took consistency but it wasn't hard",
                             "Each day I noticed small but real improvements"],
    "result_reveal":        ["By the end I couldn't believe the numbers",
                             "The final result spoke for itself",
                             "This is what consistency actually looks like"],
}

# ── Visual plan templates ──────────────────────────────────────────────────────

_VISUAL_SCENE_TEMPLATES: dict[str, list[dict]] = {
    "product": [
        {"scene": "intro",       "shot_type": "close_up",    "duration_ratio": 0.15, "text_overlay": True},
        {"scene": "problem",     "shot_type": "b_roll",      "duration_ratio": 0.20, "text_overlay": True},
        {"scene": "product_hero","shot_type": "hero_shot",   "duration_ratio": 0.25, "text_overlay": False},
        {"scene": "demo",        "shot_type": "hands_on",    "duration_ratio": 0.25, "text_overlay": True},
        {"scene": "cta",         "shot_type": "talking_head","duration_ratio": 0.15, "text_overlay": True},
    ],
    "video": [
        {"scene": "hook",        "shot_type": "talking_head","duration_ratio": 0.15, "text_overlay": True},
        {"scene": "setup",       "shot_type": "b_roll",      "duration_ratio": 0.20, "text_overlay": False},
        {"scene": "main_content","shot_type": "screen_rec",  "duration_ratio": 0.40, "text_overlay": True},
        {"scene": "summary",     "shot_type": "talking_head","duration_ratio": 0.15, "text_overlay": True},
        {"scene": "cta",         "shot_type": "overlay",     "duration_ratio": 0.10, "text_overlay": True},
    ],
}

# ── Template IDs (niche-based) ─────────────────────────────────────────────────

_TEMPLATE_POOLS: dict[str, list[str]] = {
    "product": [
        "tpl_product_minimal_v1",
        "tpl_product_bold_v2",
        "tpl_product_luxury_v3",
        "tpl_product_energetic_v4",
        "tpl_product_trust_v5",
    ],
    "video": [
        "tpl_video_clean_v1",
        "tpl_video_dynamic_v2",
        "tpl_video_cinematic_v3",
        "tpl_video_tutorial_v4",
        "tpl_video_vlog_v5",
    ],
}

# Locale → style modifier for template bias
_LOCALE_STYLE_BIAS: dict[str, int] = {
    "vi-VN": 0, "th-TH": 1, "id-ID": 1, "zh-TW": 2,
    "ja-JP": 2, "ko-KR": 3, "en-US": 4, "en-GB": 4,
    "de-DE": 2, "fr-FR": 3,
}

# Duration ranges per mode (seconds)
_DURATION_RANGES: dict[ContentMode, tuple[float, float]] = {
    ContentMode.REMAKE: (30.0, 60.0),
    ContentMode.REUP:   (15.0, 45.0),
    ContentMode.CREATE: (45.0, 90.0),
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ScriptStep:
    """One segment of the content script."""
    role:      str    # "hook" | "body" | "cta"
    label:     str    # semantic label from pattern
    text:      str    # copy text (seeded selection)
    duration:  float  # seconds allocated to this segment
    order:     int    # 0-indexed position in script

    def to_dict(self) -> dict[str, Any]:
        return {
            "role":     self.role,
            "label":    self.label,
            "text":     self.text,
            "duration": self.duration,
            "order":    self.order,
        }


@dataclass
class VisualScene:
    """One scene in the visual plan."""
    scene:          str
    shot_type:      str
    duration:       float
    text_overlay:   bool
    order:          int
    notes:          str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene":        self.scene,
            "shot_type":    self.shot_type,
            "duration":     self.duration,
            "text_overlay": self.text_overlay,
            "order":        self.order,
            "notes":        self.notes,
        }


@dataclass
class ContentPlan:
    """Full content execution plan from ContentEngine.build_plan()."""
    account_id:   str
    mode:         ContentMode
    content_type: ContentType
    template_id:  str
    duration:     float              # total seconds
    script:       list[ScriptStep]
    visual_plan:  list[VisualScene]
    pattern_key:  str               # which script pattern was used
    source:       str               # original source reference

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":   self.account_id,
            "mode":         self.mode.value,
            "content_type": self.content_type.value,
            "template_id":  self.template_id,
            "duration":     self.duration,
            "pattern_key":  self.pattern_key,
            "source":       self.source,
            "script":       [s.to_dict() for s in self.script],
            "visual_plan":  [v.to_dict() for v in self.visual_plan],
        }


# ── ContentEngine ─────────────────────────────────────────────────────────────

class ContentEngine:
    """
    Layer 6: Content Plan Generator.

    Produces deterministic ContentPlan objects per account_id.
    Reads IdentityProfile for locale/device style hints.
    Never touches fingerprints, strategy, or session logic.

    Integration:
        engine = get_content_engine()
        plan   = engine.build_plan({
            "type":       "product",
            "source":     "https://example.com/product",
            "account_id": "acc-001",
            "mode":       "create",          # optional, defaults to "create"
        }, profile=identity_profile)          # optional
    """

    def __init__(self) -> None:
        self._plan_cache: dict[str, ContentPlan] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def build_plan(
        self,
        input_data: dict[str, Any],
        profile: "IdentityProfile | None" = None,
    ) -> ContentPlan:
        """
        Build a deterministic content plan.

        Args:
            input_data: {"type", "source", "account_id", "mode" (optional)}
            profile:    IdentityProfile for locale/device style hints (optional).

        Returns:
            ContentPlan with script, visual_plan, template_id, duration.
        """
        account_id   = str(input_data["account_id"])
        source       = str(input_data.get("source", ""))
        raw_type     = str(input_data.get("type", "video")).lower()
        raw_mode     = str(input_data.get("mode", "create")).lower()

        content_type = ContentType(raw_type)   if raw_type in ContentType._value2member_map_ else ContentType.VIDEO
        mode         = ContentMode(raw_mode)   if raw_mode in ContentMode._value2member_map_ else ContentMode.CREATE

        # Cache key includes source so different source → different plan
        cache_key = f"{account_id}:{mode.value}:{content_type.value}:{source[:32]}"
        if cache_key in self._plan_cache:
            return self._plan_cache[cache_key]

        # Style hint from IdentityProfile
        locale      = profile.locale      if profile else "en-US"
        device_type = profile.device_type if profile else "mobile"

        # Build components
        pattern_key, pattern  = self._select_pattern(account_id, mode)
        duration              = self._select_duration(account_id, mode)
        template_id           = self._select_template(account_id, content_type, locale)
        script                = self._build_script(account_id, pattern_key, pattern, duration, source, mode)
        visual_plan           = self._build_visual_plan(account_id, content_type, duration, device_type)

        plan = ContentPlan(
            account_id   = account_id,
            mode         = mode,
            content_type = content_type,
            template_id  = template_id,
            duration     = duration,
            script       = script,
            visual_plan  = visual_plan,
            pattern_key  = pattern_key,
            source       = source,
        )

        self._plan_cache[cache_key] = plan

        LOGGER.info("content_plan_built", extra={
            "account_id":   account_id,
            "mode":         mode.value,
            "type":         content_type.value,
            "template_id":  template_id,
            "duration":     duration,
            "pattern":      pattern_key,
            "script_steps": len(script),
        })

        return plan

    # ── Internal builders ─────────────────────────────────────────────────────

    def _select_pattern(self, account_id: str, mode: ContentMode) -> tuple[str, list[dict]]:
        """Pick one of 3 script patterns for this mode, seeded by account_id."""
        keys       = _MODE_PATTERNS[mode]
        chosen_key = _cpick(account_id, 10, keys)
        return chosen_key, _SCRIPT_PATTERNS[chosen_key]

    def _select_duration(self, account_id: str, mode: ContentMode) -> float:
        lo, hi = _DURATION_RANGES[mode]
        raw = _cfloat(account_id, 20, lo, hi)
        # Round to nearest 5s for natural feel
        return round(raw / 5) * 5.0

    def _select_template(self, account_id: str, content_type: ContentType, locale: str) -> str:
        pool = _TEMPLATE_POOLS[content_type.value]
        # Locale biases the starting index, account_id picks the final template
        bias = _LOCALE_STYLE_BIAS.get(locale, 0)
        base = _cint(account_id, 30, 0, len(pool) - 1)
        idx  = (base + bias) % len(pool)
        return pool[idx]

    def _pick_copy(self, account_id: str, label: str, slot: int) -> str:
        """Pick a copy variant for a given label, seeded by account_id + slot."""
        variants = (
            _HOOK_VARIANTS.get(label)
            or _BODY_FILLERS.get(label)
            or _CTA_VARIANTS
        )
        if isinstance(variants, list) and len(variants) > 0:
            return _cpick(account_id, slot, variants)
        return label  # fallback: use label as placeholder

    def _build_script(
        self,
        account_id: str,
        pattern_key: str,
        pattern: list[dict],
        total_duration: float,
        source: str,
        mode: ContentMode,
    ) -> list[ScriptStep]:
        steps: list[ScriptStep] = []
        # Source fingerprint shifts copy selection so same pattern + diff source → diff text
        source_salt = int(hashlib.sha256(source.encode()).hexdigest()[:4], 16)

        for i, seg in enumerate(pattern):
            seg_duration = round(total_duration * seg["weight"], 1)
            copy_slot    = 100 + i * 13 + source_salt % 50
            text         = self._pick_copy(account_id, seg["label"], copy_slot)

            # For reup mode: prepend refresh marker on hook
            if mode == ContentMode.REUP and seg["role"] == "hook":
                text = f"[Refreshed] {text}"

            # For remake mode: append angle indicator on body
            if mode == ContentMode.REMAKE and seg["role"] == "body" and i == 1:
                angle = _cpick(account_id, 200 + i, ["new perspective", "deeper dive", "untold angle"])
                text  = f"{text} — {angle}"

            steps.append(ScriptStep(
                role     = seg["role"],
                label    = seg["label"],
                text     = text,
                duration = seg_duration,
                order    = i,
            ))

        return steps

    def _build_visual_plan(
        self,
        account_id: str,
        content_type: ContentType,
        total_duration: float,
        device_type: str,
    ) -> list[VisualScene]:
        template = _VISUAL_SCENE_TEMPLATES[content_type.value]
        scenes: list[VisualScene] = []

        # Vary shot type based on device — mobile users prefer talking_head over screen_rec
        for i, scene_def in enumerate(template):
            shot = scene_def["shot_type"]
            if device_type == "mobile" and shot == "screen_rec":
                shot = _cpick(account_id, 300 + i, ["talking_head", "b_roll", "hands_on"])

            duration = round(total_duration * scene_def["duration_ratio"], 1)

            # Per-account note variant
            note_slot = 400 + i
            notes = _cpick(account_id, note_slot, [
                f"Keep energy high in {scene_def['scene']}",
                f"Natural lighting preferred for {scene_def['scene']}",
                f"Fast cut recommended for {scene_def['scene']}",
                f"Slow reveal works well for {scene_def['scene']}",
            ])

            scenes.append(VisualScene(
                scene        = scene_def["scene"],
                shot_type    = shot,
                duration     = duration,
                text_overlay = scene_def["text_overlay"],
                order        = i,
                notes        = notes,
            ))

        return scenes

    # ── Persistence helpers ───────────────────────────────────────────────────

    def clear_cache(self, account_id: str | None = None) -> None:
        if account_id is None:
            self._plan_cache.clear()
        else:
            keys = [k for k in self._plan_cache if k.startswith(account_id + ":")]
            for k in keys:
                del self._plan_cache[k]

    def snapshot_cache(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self._plan_cache.items()}


# ── Singleton ─────────────────────────────────────────────────────────────────

_CONTENT_ENGINE: ContentEngine | None = None


def get_content_engine() -> ContentEngine:
    """Return the process-level ContentEngine singleton."""
    global _CONTENT_ENGINE
    if _CONTENT_ENGINE is None:
        _CONTENT_ENGINE = ContentEngine()
    return _CONTENT_ENGINE
