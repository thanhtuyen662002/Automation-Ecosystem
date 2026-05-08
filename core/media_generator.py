"""
Media Generator — ImageRenderer + VideoRenderer.

Input:  ContentPlan (from content_engine.py)
Output: MediaResult { images: list[str], video_path: str }

Architecture:
    ImageRenderer   — JSON-driven template → PIL image → PNG file
    VideoRenderer   — visual_plan scenes → moviepy clips → MP4 file

Design contracts:
  - JSON template drives ALL layout (no hardcoded positions)
  - 9:16 vertical (1080×1920) by default; configurable per template
  - Deterministic style per account_id (seeded colours, font sizes)
  - No uncontrolled randomness
  - Graceful degradation: missing fonts fall back to PIL default
  - Both renderers work independently

Template JSON schema (image):
    {
      "canvas": {"w": 1080, "h": 1920, "bg_color": [R, G, B]},
      "layers": [
        {"type": "background", "color": [R,G,B]},
        {"type": "image",      "src": "...", "x":0, "y":0, "w":1080, "h":960},
        {"type": "text_block",
         "text": "{{text}}",
         "x": 60, "y": 900, "w": 960, "h": null,
         "font_size": 52, "font_color": [255,255,255],
         "bg_color": [0,0,0], "bg_alpha": 180,
         "radius": 20, "padding": [24,20]}
      ]
    }

Usage:
    from core.media_generator import get_media_generator
    gen    = get_media_generator()
    result = gen.render(plan, output_dir="output/acct-001")
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from core.content_engine import ContentPlan, VisualScene, ScriptStep

LOGGER = logging.getLogger("core.media_generator")

# ── Defaults ──────────────────────────────────────────────────────────────────

CANVAS_W: int   = 1080
CANVAS_H: int   = 1920
FPS:      int   = 30
VIDEO_CODEC     = "libx264"
VIDEO_BITRATE   = "4000k"

# Built-in colour palettes (seeded per account)
_PALETTES: list[dict[str, Any]] = [
    {"bg": (15, 15, 25),   "accent": (100, 180, 255), "text": (255, 255, 255), "overlay": (0, 0, 0)},
    {"bg": (20, 10, 10),   "accent": (255, 100, 80),  "text": (255, 240, 230), "overlay": (30, 10, 5)},
    {"bg": (10, 20, 15),   "accent": (80, 220, 150),  "text": (230, 255, 240), "overlay": (5, 20, 10)},
    {"bg": (20, 15, 5),    "accent": (255, 200, 60),  "text": (255, 250, 220), "overlay": (25, 18, 0)},
    {"bg": (18, 8, 28),    "accent": (180, 100, 255), "text": (240, 220, 255), "overlay": (15, 5, 25)},
]

_FONT_SIZE_SCALES: list[float] = [0.85, 1.0, 1.15, 1.25, 0.95]


# ── PRNG (same pattern as rest of codebase) ───────────────────────────────────

def _mseed(account_id: str, slot: int) -> float:
    h = hashlib.sha256(f"mg:{account_id}:{slot}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _mpick(account_id: str, slot: int, pool: list) -> Any:
    return pool[int(_mseed(account_id, slot) * len(pool))]


def _mrgb(account_id: str, slot: int, base: tuple[int, int, int], variance: int = 30) -> tuple[int, int, int]:
    """Shift a base RGB colour deterministically per account."""
    dr = int((_mseed(account_id, slot)     - 0.5) * 2 * variance)
    dg = int((_mseed(account_id, slot + 1) - 0.5) * 2 * variance)
    db = int((_mseed(account_id, slot + 2) - 0.5) * 2 * variance)
    return (
        max(0, min(255, base[0] + dr)),
        max(0, min(255, base[1] + dg)),
        max(0, min(255, base[2] + db)),
    )


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MediaResult:
    account_id:  str
    images:      list[str]   # absolute paths to rendered PNGs
    video_path:  str         # absolute path to rendered MP4 (or "" if skipped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "images":     self.images,
            "video_path": self.video_path,
        }


# ── Default template builder ──────────────────────────────────────────────────

def _build_default_template(
    account_id: str,
    text: str,
    role: str,
    canvas_w: int = CANVAS_W,
    canvas_h: int = CANVAS_H,
) -> dict[str, Any]:
    """
    Generate a default 9:16 template dict for a given script step.
    All values derived deterministically from account_id.
    """
    palette    = _mpick(account_id, 0, _PALETTES)
    font_scale = _mpick(account_id, 1, _FONT_SIZE_SCALES)
    bg_color   = list(_mrgb(account_id, 10, palette["bg"], variance=20))
    accent     = list(_mrgb(account_id, 13, palette["accent"], variance=40))
    text_color = list(palette["text"])

    # Role-specific layout
    base_font = 52
    if role == "hook":
        y_frac, h_frac, font_mult = 0.10, 0.25, 1.20
    elif role == "cta":
        y_frac, h_frac, font_mult = 0.75, 0.20, 1.0
    else:
        y_frac, h_frac, font_mult = 0.42, 0.30, 0.90

    font_size = int(base_font * font_scale * font_mult)
    margin    = 60
    text_y    = int(canvas_h * y_frac)
    text_w    = canvas_w - margin * 2

    layers: list[dict] = [
        {
            "type":  "background",
            "color": bg_color,
        },
        {
            "type":      "gradient_bar",
            "color_top": accent,
            "color_bot": bg_color,
            "height":    int(canvas_h * 0.35),
            "y":         0,
        },
        {
            "type":       "text_block",
            "text":       text,
            "x":          margin,
            "y":          text_y,
            "w":          text_w,
            "h":          None,
            "font_size":  font_size,
            "font_color": text_color,
            "bg_color":   list(palette["overlay"]),
            "bg_alpha":   200,
            "radius":     24,
            "padding":    [28, 22],
        },
    ]

    # Hook gets a thin accent line
    if role == "hook":
        layers.append({
            "type":   "rect",
            "x":      margin,
            "y":      text_y - 12,
            "w":      120,
            "h":      6,
            "color":  accent,
            "radius": 3,
        })

    return {
        "canvas": {"w": canvas_w, "h": canvas_h, "bg_color": bg_color},
        "layers": layers,
    }


# ── ImageRenderer ─────────────────────────────────────────────────────────────

class ImageRenderer:
    """
    Render a PIL Image from a JSON template + variable data dict.

    Template keys understood:
        background  — solid fill
        gradient_bar — vertical gradient rectangle
        image       — paste a PIL Image object (passed in data["images"])
        text_block  — text with rounded-rect background
        rect        — plain coloured rectangle (optional radius)
    """

    def __init__(self) -> None:
        self._font_cache: dict[tuple[str | None, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (None, size)
        if key not in self._font_cache:
            try:
                # Try common system fonts (Windows)
                for name in [
                    "C:/Windows/Fonts/arialbd.ttf",
                    "C:/Windows/Fonts/arial.ttf",
                    "C:/Windows/Fonts/calibrib.ttf",
                ]:
                    if os.path.exists(name):
                        self._font_cache[key] = ImageFont.truetype(name, size)
                        break
                else:
                    self._font_cache[key] = ImageFont.load_default(size=size)
            except Exception:
                self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    def render_template(
        self,
        template: dict[str, Any],
        data: dict[str, Any] | None = None,
    ) -> Image.Image:
        """
        Render template dict to PIL Image.

        Args:
            template: Template dict (canvas + layers).
            data:     Variable substitution data, e.g. {"text": "...", "images": {...}}.
        """
        data = data or {}
        canvas_cfg = template.get("canvas", {})
        w = int(canvas_cfg.get("w", CANVAS_W))
        h = int(canvas_cfg.get("h", CANVAS_H))
        bg = tuple(canvas_cfg.get("bg_color", [15, 15, 25]))

        img  = Image.new("RGBA", (w, h), (*bg, 255))  # type: ignore[arg-type]
        draw = ImageDraw.Draw(img)

        for layer in template.get("layers", []):
            layer_type = layer.get("type", "")

            if layer_type == "background":
                color = tuple(layer.get("color", bg))
                draw.rectangle([(0, 0), (w, h)], fill=(*color, 255))  # type: ignore[arg-type]

            elif layer_type == "gradient_bar":
                self._draw_gradient_bar(img, layer)

            elif layer_type == "image":
                src = data.get("images", {}).get(layer.get("src", ""))
                if src is not None:
                    self._paste_image(img, src, layer)

            elif layer_type == "text_block":
                text = layer.get("text", "")
                # Variable substitution: {{key}}
                if "{{text}}" in text:
                    text = text.replace("{{text}}", str(data.get("text", "")))
                self._draw_text_block(img, draw, layer, text)

            elif layer_type == "rect":
                self._draw_rect(draw, layer)

        return img.convert("RGB")

    # ── Layer draw helpers ────────────────────────────────────────────────────

    def _draw_gradient_bar(self, img: Image.Image, layer: dict) -> None:
        top   = tuple(layer.get("color_top", [80, 80, 200]))
        bot   = tuple(layer.get("color_bot", [15, 15, 25]))
        height= int(layer.get("height", img.height // 3))
        y0    = int(layer.get("y", 0))
        w     = img.width

        bar   = Image.new("RGBA", (w, height), (0, 0, 0, 0))
        draw  = ImageDraw.Draw(bar)
        for row in range(height):
            t = row / max(height - 1, 1)
            r = int(top[0] * (1 - t) + bot[0] * t)  # type: ignore[index]
            g = int(top[1] * (1 - t) + bot[1] * t)  # type: ignore[index]
            b = int(top[2] * (1 - t) + bot[2] * t)  # type: ignore[index]
            a = int(220 * (1 - t))
            draw.line([(0, row), (w, row)], fill=(r, g, b, a))
        img.alpha_composite(bar, (0, y0))

    def _paste_image(self, canvas: Image.Image, src: Image.Image, layer: dict) -> None:
        x  = int(layer.get("x", 0))
        y  = int(layer.get("y", 0))
        lw = int(layer.get("w", canvas.width))
        lh = int(layer.get("h", canvas.height))
        resized = src.resize((lw, lh), Image.LANCZOS)
        if resized.mode == "RGBA":
            canvas.alpha_composite(resized.convert("RGBA"), (x, y))
        else:
            canvas.paste(resized.convert("RGB"), (x, y))

    def _draw_text_block(
        self,
        canvas: Image.Image,
        draw: ImageDraw.Draw,
        layer: dict,
        text: str,
    ) -> None:
        x       = int(layer.get("x", 60))
        y       = int(layer.get("y", 900))
        max_w   = int(layer.get("w", canvas.width - x * 2))
        font_sz = int(layer.get("font_size", 52))
        f_color = tuple(layer.get("font_color", [255, 255, 255]))
        bg_rgb  = tuple(layer.get("bg_color",   [0, 0, 0]))
        bg_a    = int(layer.get("bg_alpha",  180))
        radius  = int(layer.get("radius",    20))
        pad_x, pad_y = (layer.get("padding", [24, 20]) + [20, 20])[:2]

        font   = self._get_font(font_sz)
        lines  = self._wrap_text(text, font, max_w - pad_x * 2)

        line_h = font_sz + 8
        block_h= len(lines) * line_h + pad_y * 2
        block_w= min(max_w, max(
            draw.textlength(ln, font=font) + pad_x * 2
            for ln in (lines or [""])
        ))

        # Rounded-rect background
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od      = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x, y, x + int(block_w), y + block_h],
            radius  = radius,
            fill    = (*bg_rgb, bg_a),  # type: ignore[arg-type]
        )
        canvas.alpha_composite(overlay)

        # Text lines
        ty = y + pad_y
        for line in lines:
            draw.text((x + pad_x, ty), line, font=font, fill=(*f_color, 255))  # type: ignore[arg-type]
            ty += line_h

    def _draw_rect(self, draw: ImageDraw.Draw, layer: dict) -> None:
        x   = int(layer.get("x", 0))
        y   = int(layer.get("y", 0))
        w   = int(layer.get("w", 100))
        h   = int(layer.get("h", 10))
        col = tuple(layer.get("color", [255, 255, 255]))
        rad = int(layer.get("radius", 0))
        if rad > 0:
            draw.rounded_rectangle([x, y, x + w, y + h], radius=rad, fill=(*col, 255))  # type: ignore[arg-type]
        else:
            draw.rectangle([x, y, x + w, y + h], fill=(*col, 255))  # type: ignore[arg-type]

    @staticmethod
    def _wrap_text(text: str, font: Any, max_px: int) -> list[str]:
        """Word-wrap text to fit within max_px width."""
        words  = text.split()
        lines: list[str] = []
        current = ""
        dummy   = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        for word in words:
            candidate = (current + " " + word).strip()
            if dummy.textlength(candidate, font=font) <= max_px:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [text]


# ── VideoRenderer ─────────────────────────────────────────────────────────────

class VideoRenderer:
    """
    Build a video from a ContentPlan's visual_plan using moviepy 2.x.

    Each VisualScene → one ImageClip with:
      - zoom/pan effect (Ken Burns: slow scale + translate)
      - text overlay (semi-transparent rounded-rect, no external font needed)
      - FadeIn / FadeOut transitions
      - Correct duration from scene.duration
    """

    def __init__(self, image_renderer: ImageRenderer) -> None:
        self._ir = image_renderer

    def build_timeline(
        self,
        plan: "ContentPlan",
        account_id: str,
        output_path: str,
        canvas_w: int = CANVAS_W,
        canvas_h: int = CANVAS_H,
        fps: int      = FPS,
    ) -> str:
        """
        Render visual_plan to MP4.

        Args:
            plan:        ContentPlan (uses visual_plan + script for text overlays).
            account_id:  For seeded style selection.
            output_path: Destination .mp4 path.
            canvas_w/h:  Frame dimensions.
            fps:         Frames per second.

        Returns:
            Absolute path to written MP4.
        """
        from moviepy import (
            ImageClip, CompositeVideoClip, concatenate_videoclips, vfx,
        )

        # Map script roles to text for overlay (hook → first script step, etc.)
        role_text: dict[str, str] = {}
        for step in plan.script:
            role_text.setdefault(step.role, step.text)

        clips = []
        for i, scene in enumerate(plan.visual_plan):
            duration  = max(1.0, scene.duration)
            text      = role_text.get(scene.scene, "")
            if not text:
                # Fallback: pick from script by index
                if i < len(plan.script):
                    text = plan.script[i].text

            # 1. Render base PIL image for this scene
            template  = _build_default_template(
                account_id, text,
                role = _scene_to_role(scene.scene),
                canvas_w = canvas_w,
                canvas_h = canvas_h,
            )
            pil_img   = self._ir.render_template(template)
            arr       = np.array(pil_img, dtype=np.uint8)

            # 2. Build ImageClip
            clip = ImageClip(arr, duration=duration)

            # 3. Ken Burns: seeded zoom + pan
            clip = self._apply_zoom_pan(clip, account_id, i, canvas_w, canvas_h)

            # 4. Fade transitions
            fade = min(0.4, duration * 0.15)
            clip = clip.with_effects([vfx.FadeIn(fade), vfx.FadeOut(fade)])

            clips.append(clip)

        if not clips:
            raise ValueError("No scenes to render")

        final = concatenate_videoclips(clips, method="compose")
        final = final.with_fps(fps)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        final.write_videofile(
            output_path,
            codec     = VIDEO_CODEC,
            bitrate   = VIDEO_BITRATE,
            logger    = None,      # suppress moviepy progress bar
            audio     = False,
        )
        final.close()
        for c in clips:
            c.close()

        LOGGER.info("video_rendered", extra={
            "account_id":  account_id,
            "output_path": output_path,
            "scenes":      len(clips),
            "duration":    sum(s.duration for s in plan.visual_plan),
        })
        return os.path.abspath(output_path)

    def _apply_zoom_pan(
        self,
        clip: Any,
        account_id: str,
        scene_idx: int,
        canvas_w: int,
        canvas_h: int,
    ) -> Any:
        """
        Apply a seeded Ken Burns zoom/pan using moviepy's frame_function transform.
        Zoom range: 1.00 → 1.06 (subtle; avoids distracting motion).
        Pan: ±2% horizontal, ±2% vertical shift.
        """
        # Seeded parameters
        zoom_end   = 1.02 + _mseed(account_id, 500 + scene_idx) * 0.06    # 1.02–1.08
        pan_x_end  = (_mseed(account_id, 510 + scene_idx) - 0.5) * 0.04   # ±2%
        pan_y_end  = (_mseed(account_id, 520 + scene_idx) - 0.5) * 0.04   # ±2%
        duration   = clip.duration

        def frame_fn(get_frame: Any, t: float) -> np.ndarray:
            frame = get_frame(t)
            progress = t / max(duration, 0.001)
            zoom   = 1.0 + (zoom_end - 1.0) * progress
            px     = pan_x_end * progress
            py     = pan_y_end * progress

            fh, fw = frame.shape[:2]
            # Scale up slightly then crop back to original size
            new_w  = int(fw * zoom)
            new_h  = int(fh * zoom)
            pil_f  = Image.fromarray(frame)
            pil_f  = pil_f.resize((new_w, new_h), Image.BILINEAR)
            # Crop centre + pan offset
            ox = (new_w - fw) // 2 + int(fw * px)
            oy = (new_h - fh) // 2 + int(fh * py)
            ox = max(0, min(ox, new_w - fw))
            oy = max(0, min(oy, new_h - fh))
            cropped = np.array(pil_f)[oy:oy + fh, ox:ox + fw]
            return cropped

        return clip.transform(frame_fn, apply_to="video")


# ── MediaGenerator ────────────────────────────────────────────────────────────

class MediaGenerator:
    """
    Unified entry point: ContentPlan → MediaResult.

    render() produces:
      - One PNG per script step (ImageRenderer)
      - One MP4 from visual_plan  (VideoRenderer)
    """

    def __init__(self) -> None:
        self._ir = ImageRenderer()
        self._vr = VideoRenderer(self._ir)

    def render(
        self,
        plan: "ContentPlan",
        output_dir: str        = "output",
        render_video: bool     = True,
        canvas_w: int          = CANVAS_W,
        canvas_h: int          = CANVAS_H,
        fps: int               = FPS,
    ) -> MediaResult:
        """
        Full render pipeline.

        Args:
            plan:         ContentPlan from ContentEngine.
            output_dir:   Directory for all output files.
            render_video: Set False to skip video (images only).
            canvas_w/h:   Frame dimensions.
            fps:          Video frame rate.

        Returns:
            MediaResult with image paths and video path.
        """
        account_id = plan.account_id
        out        = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # ── 1. Render images (one per script step) ────────────────────────────
        image_paths: list[str] = []
        for step in plan.script:
            template = _build_default_template(
                account_id, step.text, step.role,
                canvas_w = canvas_w,
                canvas_h = canvas_h,
            )
            img  = self._ir.render_template(template)
            fname= out / f"{account_id}_{step.role}_{step.order:02d}.png"
            img.save(str(fname), format="PNG", optimize=False)
            image_paths.append(str(fname.resolve()))
            LOGGER.debug("image_saved", extra={"path": str(fname)})

        # ── 2. Render video ───────────────────────────────────────────────────
        video_path = ""
        if render_video:
            vpath = out / f"{account_id}_video.mp4"
            video_path = self._vr.build_timeline(
                plan, account_id, str(vpath), canvas_w, canvas_h, fps,
            )

        LOGGER.info("media_render_complete", extra={
            "account_id":  account_id,
            "images":      len(image_paths),
            "video_path":  video_path,
        })

        return MediaResult(
            account_id = account_id,
            images     = image_paths,
            video_path = video_path,
        )

    def render_image_only(
        self,
        account_id: str,
        text: str,
        role: str,
        output_path: str,
        canvas_w: int = CANVAS_W,
        canvas_h: int = CANVAS_H,
    ) -> str:
        """Render a single image directly (no ContentPlan required)."""
        template = _build_default_template(account_id, text, role, canvas_w, canvas_h)
        img      = self._ir.render_template(template)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        img.save(output_path, format="PNG")
        return os.path.abspath(output_path)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scene_to_role(scene: str) -> str:
    """Map visual scene name to script role for template style selection."""
    mapping = {
        "hook":       "hook",
        "intro":      "hook",
        "cta":        "cta",
        "problem":    "body",
        "setup":      "body",
        "main_content": "body",
        "summary":    "body",
        "demo":       "body",
        "product_hero": "body",
    }
    return mapping.get(scene, "body")


# ── Singleton ─────────────────────────────────────────────────────────────────

_MEDIA_GENERATOR: MediaGenerator | None = None


def get_media_generator() -> MediaGenerator:
    """Return the process-level MediaGenerator singleton."""
    global _MEDIA_GENERATOR
    if _MEDIA_GENERATOR is None:
        _MEDIA_GENERATOR = MediaGenerator()
    return _MEDIA_GENERATOR
