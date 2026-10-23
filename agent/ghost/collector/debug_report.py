"""Workflow Debug Report Generator for DigitalSoul.

Inspired by Microsoft Record-and-Run's visual replay/annotation approach:
produces a self-contained HTML timeline for each workflow execution showing
every step with:
  - Full annotated screenshot (bounding box + ripple + badge)
  - Focus crop (zoomed-in, pixel-perfect view of the target)
  - Set-of-Marks (SoM) overlay image
  - Step metadata: action kind, label, coordinates, timestamp
  - Pass/fail status with error messages

The report is written to:
    .ghost/memory/screenshots/<workflow_name>/debug_report.html

Open it in any browser for a side-by-side step-by-step debug trace.
"""
from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None


# ---------------------------------------------------------------------------
# Step Record
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    """Data captured for a single executed step."""
    index: int
    action: str                             # "click", "set_text", "press_key", …
    label: str                              # human label / target name
    value: Optional[str] = None            # text value for set_text steps
    coords: Optional[Tuple[int, int]] = None
    bbox: Optional[Tuple[int, int, int, int]] = None
    status: str = "ok"                     # "ok" | "fallback" | "error"
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    # PIL images (optional – not stored if PIL unavailable)
    full_image: Optional[Any] = None       # annotated full screenshot
    crop_image: Optional[Any] = None       # focus crop
    som_image: Optional[Any] = None        # Set-of-Marks overlay


# ---------------------------------------------------------------------------
# Report Builder
# ---------------------------------------------------------------------------

_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    padding: 24px;
  }
  h1 {
    font-size: 1.4rem;
    font-weight: 700;
    color: #e6edf3;
    margin-bottom: 6px;
  }
  .meta { font-size: 0.8rem; color: #8b949e; margin-bottom: 24px; }
  .stats {
    display: flex; gap: 16px; margin-bottom: 24px;
    flex-wrap: wrap;
  }
  .stat-pill {
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
  }
  .stat-ok    { background:#1a3a2a; color:#3fb950; border:1px solid #238636; }
  .stat-fall  { background:#2d2208; color:#e3b341; border:1px solid #9e6a03; }
  .stat-err   { background:#3a1c1c; color:#f85149; border:1px solid #da3633; }
  .stat-total { background:#161b22; color:#8b949e; border:1px solid #30363d; }

  .step-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    margin-bottom: 18px;
    overflow: hidden;
    transition: box-shadow .2s;
  }
  .step-card:hover { box-shadow: 0 0 0 1px #388bfd55; }
  .step-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: #1c2128;
    border-bottom: 1px solid #30363d;
    flex-wrap: wrap;
  }
  .step-num {
    font-size: 0.75rem;
    font-weight: 700;
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 2px 7px;
    color: #8b949e;
    min-width: 38px;
    text-align: center;
  }
  .action-badge {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 2px 9px;
    border-radius: 12px;
    text-transform: uppercase;
    letter-spacing: .04em;
  }
  .action-click    { background:#1a3a2a; color:#3fb950; }
  .action-set_text { background:#1b2b45; color:#58a6ff; }
  .action-press_key{ background:#2d2208; color:#e3b341; }
  .action-open_app { background:#1f1b35; color:#d2a8ff; }
  .action-other    { background:#21262d; color:#8b949e; }
  .step-label { font-size: 0.9rem; color: #e6edf3; flex:1; min-width:120px; }
  .step-value { font-size: 0.8rem; color: #8b949e; font-style: italic; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status-ok   { margin-left:auto; color:#3fb950; font-size:0.75rem; font-weight:600; }
  .status-fall { margin-left:auto; color:#e3b341; font-size:0.75rem; font-weight:600; }
  .status-err  { margin-left:auto; color:#f85149; font-size:0.75rem; font-weight:600; }
  .ts          { font-size:0.72rem; color:#484f58; }

  .step-body {
    display: flex;
    gap: 0;
    overflow: hidden;
  }
  .img-panel {
    flex: 1;
    padding: 10px;
    border-right: 1px solid #21262d;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .img-panel:last-child { border-right: none; }
  .img-label {
    font-size: 0.68rem;
    font-weight: 600;
    color: #484f58;
    text-transform: uppercase;
    letter-spacing: .06em;
  }
  .img-panel img {
    max-width: 100%;
    border-radius: 5px;
    border: 1px solid #30363d;
    cursor: zoom-in;
  }
  .img-panel img:active { cursor: zoom-out; }
  .coords { font-size: 0.72rem; color: #484f58; margin-top:2px; }
  .error-block {
    margin: 8px 14px 10px;
    padding: 8px 12px;
    background: #3a1c1c;
    border: 1px solid #da363344;
    border-radius: 6px;
    font-size: 0.78rem;
    color: #f85149;
    font-family: monospace;
  }
  .no-img { color:#484f58; font-size:0.78rem; padding:10px 0; }

  /* Lightbox */
  #lb-overlay {
    display:none; position:fixed; inset:0; background:#000d;
    z-index:9999; align-items:center; justify-content:center;
  }
  #lb-overlay.open { display:flex; }
  #lb-overlay img  { max-width:95vw; max-height:95vh; border-radius:8px; }
</style>
"""

_LIGHTBOX_JS = """
<script>
  const lbOverlay = document.getElementById('lb-overlay');
  const lbImg     = document.getElementById('lb-img');
  document.querySelectorAll('.img-panel img').forEach(img => {
    img.addEventListener('click', () => {
      lbImg.src = img.src;
      lbOverlay.classList.add('open');
    });
  });
  lbOverlay.addEventListener('click', () => lbOverlay.classList.remove('open'));
</script>
"""


def _b64(pil_img: Any, max_w: int = 900) -> str:
    """Encode PIL image as base64 PNG data-URI, downscaling if wider than max_w."""
    if Image is None or pil_img is None:
        return ""
    img = pil_img
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _action_class(action: str) -> str:
    known = {"click", "set_text", "press_key", "open_app"}
    return action if action in known else "other"


def _status_class(status: str) -> str:
    return {"ok": "ok", "fallback": "fall", "error": "err"}.get(status, "ok")


class WorkflowDebugReport:
    """Accumulates step records and renders a self-contained HTML report."""

    def __init__(self, workflow_name: str, output_dir: Path):
        self.workflow_name = workflow_name
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.steps: List[StepRecord] = []
        self._started_at = time.time()

    def add_step(self, record: StepRecord) -> None:
        self.steps.append(record)

    def save(self) -> Path:
        """Render and write the HTML report. Returns the path."""
        out = self.output_dir / "debug_report.html"
        out.write_text(self._render(), encoding="utf-8")
        return out

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> str:
        n_ok   = sum(1 for s in self.steps if s.status == "ok")
        n_fall = sum(1 for s in self.steps if s.status == "fallback")
        n_err  = sum(1 for s in self.steps if s.status == "error")
        elapsed = time.time() - self._started_at

        cards = "\n".join(self._render_step(s) for s in self.steps)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DigitalSoul · {self.workflow_name} · Debug Report</title>
{_STYLE}
</head>
<body>
<h1>🤖 DigitalSoul — Workflow Debug Report</h1>
<div class="meta">Workflow: <strong>{self.workflow_name}</strong> &nbsp;·&nbsp;
{len(self.steps)} steps &nbsp;·&nbsp; {elapsed:.1f}s total &nbsp;·&nbsp;
{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._started_at))}</div>

<div class="stats">
  <div class="stat-pill stat-total">Total: {len(self.steps)}</div>
  <div class="stat-pill stat-ok">✓ OK: {n_ok}</div>
  <div class="stat-pill stat-fall">⚡ Visual fallback: {n_fall}</div>
  <div class="stat-pill stat-err">✗ Error: {n_err}</div>
</div>

{cards}

<!-- Lightbox -->
<div id="lb-overlay"><img id="lb-img" src="" alt=""></div>
{_LIGHTBOX_JS}
</body>
</html>"""

    def _render_step(self, s: StepRecord) -> str:
        ac = _action_class(s.action)
        sc = _status_class(s.status)
        status_icon = {"ok": "✓ OK", "fallback": "⚡ Fallback", "error": "✗ Error"}.get(s.status, "?")
        ts_str = time.strftime("%H:%M:%S", time.localtime(s.timestamp))

        value_html = ""
        if s.value:
            safe_v = s.value[:40].replace("<", "&lt;").replace(">", "&gt;")
            value_html = f'<span class="step-value">"{safe_v}{"…" if len(s.value) > 40 else ""}"</span>'

        coords_html = ""
        if s.coords:
            coords_html = f'<div class="coords">coords: ({s.coords[0]}, {s.coords[1]})</div>'

        header = f"""<div class="step-header">
  <span class="step-num">#{s.index}</span>
  <span class="action-badge action-{ac}">{s.action}</span>
  <span class="step-label">{s.label[:60].replace('<','&lt;')}</span>
  {value_html}
  <span class="ts">{ts_str}</span>
  <span class="status-{sc}">{status_icon}</span>
</div>"""

        img_panels = self._render_images(s, coords_html)

        error_html = ""
        if s.error:
            safe_err = s.error[:300].replace("<", "&lt;")
            error_html = f'<div class="error-block">⚠ {safe_err}</div>'

        return f"""<div class="step-card">
{header}
<div class="step-body">{img_panels}</div>
{error_html}
</div>"""

    def _render_images(self, s: StepRecord, coords_html: str) -> str:
        panels = []

        if s.full_image is not None and Image is not None:
            uri = _b64(s.full_image, max_w=800)
            panels.append(
                f'<div class="img-panel"><div class="img-label">Annotated</div>'
                f'<img src="{uri}" alt="annotated">{coords_html}</div>'
            )
        else:
            panels.append('<div class="img-panel"><div class="img-label">Annotated</div>'
                          '<div class="no-img">No screenshot</div></div>')

        if s.crop_image is not None and Image is not None:
            uri = _b64(s.crop_image, max_w=500)
            panels.append(
                f'<div class="img-panel"><div class="img-label">Focus Crop</div>'
                f'<img src="{uri}" alt="crop"></div>'
            )

        if s.som_image is not None and Image is not None:
            uri = _b64(s.som_image, max_w=500)
            panels.append(
                f'<div class="img-panel"><div class="img-label">Set-of-Marks</div>'
                f'<img src="{uri}" alt="som"></div>'
            )

        return "".join(panels)
