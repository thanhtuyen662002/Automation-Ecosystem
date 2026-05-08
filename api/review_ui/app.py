"""
Review UI — Standalone FastAPI application for content queue review.

Endpoints:
    GET  /              → HTML dashboard (queue list)
    GET  /queue         → JSON queue list
    GET  /preview/{id}  → HTML preview page for one item
    GET  /media/{id}    → Serve video/image file
    POST /approve       → Mark item approved
    POST /edit          → Patch caption / text_overlay on an item

Store: JSON file (queue.json) — no external DB required.

Integration:
    ContentPlan  → queue items carry plan metadata
    MediaResult  → queue items carry rendered file paths

Run standalone:
    uvicorn api.review_ui.app:app --reload --port 8100
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("review_ui")

# ── Config ────────────────────────────────────────────────────────────────────

_HERE    = Path(__file__).parent
QUEUE_FILE = Path(os.environ.get("REVIEW_QUEUE_FILE", str(_HERE / "queue.json")))
MEDIA_ROOT = Path(os.environ.get("REVIEW_MEDIA_ROOT", str(_HERE.parent.parent / "output")))


# ── Queue store ───────────────────────────────────────────────────────────────

def _load_queue() -> list[dict[str, Any]]:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_queue(items: list[dict[str, Any]]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_item(item_id: str) -> dict[str, Any]:
    for item in _load_queue():
        if item.get("id") == item_id:
            return item
    raise HTTPException(status_code=404, detail=f"Item {item_id!r} not found")


def _update_item(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    items = _load_queue()
    for item in items:
        if item.get("id") == item_id:
            item.update(patch)
            item["updated_at"] = time.time()
            _save_queue(items)
            return item
    raise HTTPException(status_code=404, detail=f"Item {item_id!r} not found")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class QueueItem(BaseModel):
    id:            str
    account_id:    str
    status:        str                          # pending | approved | rejected | edited
    caption:       str
    text_overlay:  str
    video_path:    str
    images:        list[str] = Field(default_factory=list)
    template_id:   str       = ""
    mode:          str       = "create"
    content_type:  str       = "video"
    duration:      float     = 0.0
    created_at:    float     = Field(default_factory=time.time)
    updated_at:    float     = Field(default_factory=time.time)
    plan_summary:  dict[str, Any] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    id: str


class EditRequest(BaseModel):
    id:           str
    caption:      str | None = None
    text_overlay: str | None = None


class EnqueueRequest(BaseModel):
    """Used programmatically to push a new item into the queue."""
    account_id:   str
    caption:      str
    text_overlay: str = ""
    video_path:   str = ""
    images:       list[str] = Field(default_factory=list)
    template_id:  str = ""
    mode:         str = "create"
    content_type: str = "video"
    duration:     float = 0.0
    plan_summary: dict[str, Any] = Field(default_factory=dict)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Content Review UI",
    description = "Minimal review interface for generated content queue",
    version     = "1.0.0",
)

# Serve output directory as /files (for video/image preview)
if MEDIA_ROOT.exists():
    app.mount("/files", StaticFiles(directory=str(MEDIA_ROOT)), name="files")


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _status_badge(status: str) -> str:
    colors = {
        "pending":  "#f59e0b",
        "approved": "#10b981",
        "rejected": "#ef4444",
        "edited":   "#6366f1",
    }
    color = colors.get(status, "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:600;text-transform:uppercase">{status}</span>'


def _page(title: str, body: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} — Content Review</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:     #0f1117;
      --surface:#1a1d27;
      --border: #2a2d3a;
      --text:   #e4e6f0;
      --muted:  #8b90a7;
      --accent: #6366f1;
      --green:  #10b981;
      --red:    #ef4444;
      --yellow: #f59e0b;
      --radius: 12px;
    }}
    body {{
      font-family: system-ui,-apple-system,sans-serif;
      background: var(--bg); color: var(--text);
      min-height: 100vh; padding: 0;
    }}
    nav {{
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 14px 32px; display: flex; align-items: center; gap: 16px;
    }}
    nav a {{ color: var(--text); text-decoration: none; font-weight: 600; font-size: 18px; }}
    nav .pill {{
      background: var(--accent); color: #fff; border-radius: 99px;
      padding: 2px 10px; font-size: 12px; font-weight: 700;
    }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 32px 24px; }}
    h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 24px; }}
    h2 {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; }}
    .card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 20px; margin-bottom: 16px;
    }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill,minmax(320px,1fr)); gap: 16px; }}
    .meta {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
    .actions {{ margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .btn {{
      padding: 8px 18px; border: none; border-radius: 8px; font-size: 14px;
      font-weight: 600; cursor: pointer; text-decoration: none; display: inline-block;
      transition: opacity .15s;
    }}
    .btn:hover {{ opacity: .85; }}
    .btn-primary  {{ background: var(--accent); color: #fff; }}
    .btn-success  {{ background: var(--green);  color: #fff; }}
    .btn-danger   {{ background: var(--red);    color: #fff; }}
    .btn-outline  {{
      background: transparent; color: var(--text);
      border: 1px solid var(--border);
    }}
    input, textarea {{
      width: 100%; background: var(--bg); border: 1px solid var(--border);
      border-radius: 8px; color: var(--text); padding: 10px 14px;
      font-size: 14px; font-family: inherit; resize: vertical;
    }}
    input:focus, textarea:focus {{
      outline: none; border-color: var(--accent);
    }}
    label {{ font-size: 13px; color: var(--muted); display: block; margin-bottom: 6px; }}
    .field {{ margin-bottom: 16px; }}
    video {{
      width: 100%; max-width: 360px; border-radius: var(--radius);
      background: #000; display: block;
    }}
    .preview-layout {{
      display: grid; grid-template-columns: 360px 1fr; gap: 32px; align-items: start;
    }}
    @media(max-width:760px) {{ .preview-layout {{ grid-template-columns: 1fr; }} }}
    .tag {{
      display: inline-block; font-size: 11px; padding: 2px 8px;
      border-radius: 99px; border: 1px solid var(--border); color: var(--muted);
      margin-right: 6px;
    }}
    .empty {{ text-align: center; padding: 64px; color: var(--muted); }}
    .toast {{
      position: fixed; bottom: 24px; right: 24px;
      background: var(--green); color: #fff;
      padding: 12px 20px; border-radius: 10px;
      font-weight: 600; font-size: 14px;
      display: none; z-index: 999;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px; }}
    th {{ color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }}
    tr:hover td {{ background: rgba(255,255,255,.02); }}
  </style>
</head>
<body>
  <nav>
    <a href="/">&#127916; Review UI</a>
    <span class="pill">Content Queue</span>
  </nav>
  <main>
    {body}
  </main>
  <div class="toast" id="toast"></div>
  <script>
    function showToast(msg, color) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.style.background = color || '#10b981';
      t.style.display = 'block';
      setTimeout(() => t.style.display = 'none', 2500);
    }}
    async function postJSON(url, body) {{
      const r = await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body)
      }});
      return r.json();
    }}
    async function approve(id) {{
      const r = await postJSON('/approve', {{id}});
      if (r.status === 'approved') {{ showToast('Approved!'); setTimeout(() => location.reload(), 800); }}
      else showToast(r.detail || 'Error', '#ef4444');
    }}
    async function reject(id) {{
      if (!confirm('Reject this item?')) return;
      const r = await postJSON('/approve', {{id, _reject: true}});
      showToast('Rejected', '#ef4444'); setTimeout(() => location.reload(), 800);
    }}
    async function submitEdit(id) {{
      const caption = document.getElementById('caption_' + id)?.value;
      const overlay = document.getElementById('overlay_' + id)?.value;
      const r = await postJSON('/edit', {{id, caption, text_overlay: overlay}});
      if (r.id) showToast('Saved!');
      else showToast(r.detail || 'Error', '#ef4444');
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    """Main dashboard — shows full queue as cards."""
    items = _load_queue()
    pending  = [i for i in items if i.get("status") == "pending"]
    approved = [i for i in items if i.get("status") == "approved"]
    others   = [i for i in items if i.get("status") not in ("pending", "approved")]

    def card(item: dict) -> str:
        vid = item.get("video_path", "")
        vid_rel = ""
        if vid and Path(vid).exists():
            try:
                vid_rel = "/files/" + str(Path(vid).relative_to(MEDIA_ROOT)).replace("\\", "/")
            except ValueError:
                vid_rel = ""

        preview_thumb = ""
        if vid_rel:
            preview_thumb = f"""
            <video src="{vid_rel}" style="width:100%;max-height:160px;object-fit:cover;
              border-radius:8px;margin-bottom:12px" muted preload="metadata"></video>"""

        duration = item.get("duration", 0)
        return f"""
        <div class="card">
          {preview_thumb}
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
            <div>
              <strong style="font-size:15px">{item.get('account_id','')}</strong>
              {_status_badge(item.get('status',''))}
            </div>
            <span class="tag">{item.get('mode','')}</span>
          </div>
          <div class="meta" style="margin-top:8px">
            <span class="tag">{item.get('content_type','')}</span>
            <span class="tag">{duration:.0f}s</span>
            <span class="tag">{item.get('template_id','')}</span>
          </div>
          <p style="margin-top:10px;font-size:14px;color:var(--muted);
             overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px"
             title="{item.get('caption','')}">
            {item.get('caption','')[:80]}
          </p>
          <div class="actions">
            <a href="/preview/{item['id']}" class="btn btn-primary">Preview / Edit</a>
            <button class="btn btn-success" onclick="approve('{item['id']}')">Approve</button>
            <button class="btn btn-danger btn-outline" onclick="reject('{item['id']}')">Reject</button>
          </div>
        </div>"""

    def section(title: str, lst: list) -> str:
        if not lst:
            return ""
        cards = "".join(card(i) for i in lst)
        return f"<h2>{title} ({len(lst)})</h2><div class='grid'>{cards}</div><br>"

    if not items:
        body = "<div class='empty'><h2>Queue is empty</h2><p>Use POST /enqueue to add items.</p></div>"
    else:
        body = (
            f"<h1>Content Queue <small style='font-size:14px;color:var(--muted)'>({len(items)} total)</small></h1>"
            + section("Pending Review", pending)
            + section("Approved", approved)
            + section("Other", others)
        )

    return _page("Dashboard", body)


@app.get("/queue", response_model=list[dict[str, Any]])
async def get_queue(status: str | None = None) -> list[dict[str, Any]]:
    """
    Return full queue as JSON array.
    Optional ?status=pending|approved|rejected|edited filter.
    """
    items = _load_queue()
    if status:
        items = [i for i in items if i.get("status") == status]
    return items


@app.get("/preview/{item_id}", response_class=HTMLResponse, include_in_schema=False)
async def preview(item_id: str) -> HTMLResponse:
    """Full preview + inline edit page for one queue item."""
    item = _get_item(item_id)

    vid = item.get("video_path", "")
    vid_rel = ""
    if vid and Path(vid).exists():
        try:
            vid_rel = "/files/" + str(Path(vid).relative_to(MEDIA_ROOT)).replace("\\", "/")
        except ValueError:
            vid_rel = ""

    video_block = (
        f'<video src="{vid_rel}" controls style="width:100%;border-radius:12px"></video>'
        if vid_rel else
        '<div style="background:#111;border-radius:12px;aspect-ratio:9/16;'
        'display:flex;align-items:center;justify-content:center;color:var(--muted)">'
        'No video available</div>'
    )

    # Image thumbnails
    images_html = ""
    imgs = item.get("images", [])
    if imgs:
        thumbs = "".join(
            f'<img src="/media_img/{os.path.basename(p)}" '
            f'style="height:80px;border-radius:6px;object-fit:cover;cursor:pointer" '
            f'onclick="window.open(this.src)" title="{p}">'
            for p in imgs if Path(p).exists()
        )
        images_html = f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">{thumbs}</div>'

    plan = item.get("plan_summary", {})
    plan_html = ""
    if plan:
        rows = "".join(
            f"<tr><td><code>{k}</code></td><td>{v}</td></tr>"
            for k, v in plan.items() if not isinstance(v, (list, dict))
        )
        plan_html = f"<table style='margin-top:16px'><tbody>{rows}</tbody></table>"

    body = f"""
    <div style="margin-bottom:20px;display:flex;align-items:center;gap:12px">
      <a href="/" class="btn btn-outline">&larr; Back</a>
      <h1 style="margin:0">Preview: {item.get('account_id','')}</h1>
      {_status_badge(item.get('status',''))}
    </div>

    <div class="preview-layout">
      <div>
        {video_block}
        {images_html}
      </div>

      <div>
        <div class="card">
          <h2>Edit Content</h2>

          <div class="field">
            <label>Caption</label>
            <textarea id="caption_{item_id}" rows="4">{item.get('caption','')}</textarea>
          </div>

          <div class="field">
            <label>Text Overlay</label>
            <input id="overlay_{item_id}" type="text" value="{item.get('text_overlay','')}">
          </div>

          <div class="actions">
            <button class="btn btn-primary" onclick="submitEdit('{item_id}')">Save Changes</button>
            <button class="btn btn-success" onclick="approve('{item_id}')">Approve</button>
            <button class="btn btn-danger btn-outline" onclick="reject('{item_id}')">Reject</button>
          </div>
        </div>

        <div class="card">
          <h2>Item Details</h2>
          <div class="meta">
            <div style="margin-bottom:4px">
              <span class="tag">Mode: {item.get('mode','')}</span>
              <span class="tag">Type: {item.get('content_type','')}</span>
              <span class="tag">Duration: {item.get('duration',0):.0f}s</span>
            </div>
            <div>Template: <code style="font-size:12px">{item.get('template_id','')}</code></div>
            <div style="margin-top:4px;word-break:break-all">
              ID: <code style="font-size:11px">{item_id}</code>
            </div>
          </div>
          {plan_html}
        </div>
      </div>
    </div>

    <script>
      function approve(id) {{
        fetch('/approve', {{method:'POST',headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{id}})}})
          .then(r=>r.json()).then(r=>{{
            showToast(r.status==='approved'?'Approved!':'Error: '+r.detail,
                      r.status==='approved'?'#10b981':'#ef4444');
            setTimeout(()=>location.href='/',1000);
          }});
      }}
      function reject(id) {{
        if(!confirm('Reject this item?')) return;
        fetch('/approve', {{method:'POST',headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{id,_reject:true}})}})
          .then(()=>{{ showToast('Rejected','#ef4444'); setTimeout(()=>location.href='/',1000); }});
      }}
      async function submitEdit(id) {{
        const caption = document.getElementById('caption_'+id)?.value;
        const overlay = document.getElementById('overlay_'+id)?.value;
        const r = await fetch('/edit', {{method:'POST',
          headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{id,caption,text_overlay:overlay}})}});
        const data = await r.json();
        showToast(data.id?'Saved!':'Error','#6366f1');
      }}
    </script>
    """
    return _page(f"Preview {item_id}", body)


@app.post("/approve")
async def approve(request: Request) -> dict[str, Any]:
    """
    Approve or reject a queue item.

    Body: {"id": "...", "_reject": false}
    """
    body    = await request.json()
    item_id = body.get("id", "")
    reject  = bool(body.get("_reject", False))
    if not item_id:
        raise HTTPException(status_code=422, detail="id required")
    new_status = "rejected" if reject else "approved"
    return _update_item(item_id, {"status": new_status})


@app.post("/edit")
async def edit(req: EditRequest) -> dict[str, Any]:
    """
    Patch caption and/or text_overlay on a queue item.
    Only provided (non-None) fields are updated.
    Sets status to 'edited'.
    """
    patch: dict[str, Any] = {"status": "edited"}
    if req.caption is not None:
        patch["caption"] = req.caption
    if req.text_overlay is not None:
        patch["text_overlay"] = req.text_overlay
    return _update_item(req.id, patch)


@app.post("/enqueue", response_model=dict[str, Any])
async def enqueue(req: EnqueueRequest) -> dict[str, Any]:
    """
    Add a new item to the review queue.
    Called programmatically after ContentEngine + MediaGenerator finish.
    """
    items = _load_queue()
    item: dict[str, Any] = {
        "id":           str(uuid.uuid4()),
        "status":       "pending",
        "account_id":   req.account_id,
        "caption":      req.caption,
        "text_overlay": req.text_overlay,
        "video_path":   req.video_path,
        "images":       req.images,
        "template_id":  req.template_id,
        "mode":         req.mode,
        "content_type": req.content_type,
        "duration":     req.duration,
        "plan_summary": req.plan_summary,
        "created_at":   time.time(),
        "updated_at":   time.time(),
    }
    items.append(item)
    _save_queue(items)
    LOGGER.info("item_enqueued", extra={"id": item["id"], "account_id": req.account_id})
    return item


@app.delete("/queue/{item_id}", response_model=dict[str, Any])
async def delete_item(item_id: str) -> dict[str, Any]:
    """Remove an item from the queue permanently."""
    items = _load_queue()
    remaining = [i for i in items if i.get("id") != item_id]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail=f"Item {item_id!r} not found")
    _save_queue(remaining)
    return {"deleted": item_id}


@app.get("/media/{item_id}", include_in_schema=False)
async def serve_media(item_id: str) -> FileResponse:
    """Serve video file for a queue item by id."""
    item = _get_item(item_id)
    path = item.get("video_path", "")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    mime = mimetypes.guess_type(path)[0] or "video/mp4"
    return FileResponse(path, media_type=mime)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "queue_file": str(QUEUE_FILE)}
