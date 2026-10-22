"""DigitalSoul Local Agent (Loopback FastAPI Service).

Unifies M1 Learning & Intelligence with M2 Execution & Automation.
Binds strictly to 127.0.0.1:7777.
"""

from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from agent.ghost.api.learn_routes import router as learn_router
from agent.ghost.execution.runtime import Runtime
from agent.ghost.execution.recovery import RunStore
from agent.ghost.safety.policy import acme_policy
from agent.ghost.drivers.browser import BrowserDriver
from agent.ghost.api.run_routes import run_router


app = FastAPI(
    title="DigitalSoul Agent",
    description="Local-first deterministic AI workflow ghost.",
    version="1.1.0",
)

# Defend against DNS rebinding and restrict allowed origins
ALLOWED_ORIGINS = [
    "http://127.0.0.1:7777",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "chrome-extension://*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Extension and local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize M2 Execution Runtime
data_dir = Path(".ghost")
data_dir.mkdir(parents=True, exist_ok=True)
policy = acme_policy()
store = RunStore(data_dir / "m2.sqlite3")
driver = BrowserDriver(policy, profile=str(data_dir / "chrome"), headless=True)
runtime = Runtime(driver, policy, store)
app.state.runtime = runtime


@app.get("/", response_class=HTMLResponse)
def index_page():
    """Interactive landing page with links to API docs and demo apps."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DigitalSoul Agent</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #0c0d12; color: #f0f2f5; margin: 0; padding: 40px; display: flex; justify-content: center; align-items: center; min-height: 80vh; }
    .card { background: #161822; border: 1px solid #282c3f; border-radius: 16px; padding: 36px; max-width: 580px; width: 100%; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    h1 { margin-top: 0; font-size: 24px; color: #fff; display: flex; align-items: center; gap: 10px; }
    .status-badge { background: #10b98122; color: #10b981; border: 1px solid #10b98155; padding: 4px 10px; border-radius: 20px; font-size: 13px; font-weight: 600; }
    p { color: #94a3b8; line-height: 1.6; }
    .links { display: flex; flex-direction: column; gap: 12px; margin-top: 24px; }
    .link-item { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; background: #1f2233; border: 1px solid #2d334a; border-radius: 10px; text-decoration: none; color: #e2e8f0; transition: all 0.2s; }
    .link-item:hover { background: #272b42; border-color: #6366f1; transform: translateY(-1px); }
    .link-item strong { color: #fff; }
    .link-item span { font-size: 13px; color: #818cf8; }
  </style>
</head>
<body>
  <div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px;">
      <h1>DigitalSoul Agent</h1>
      <span class="status-badge">● Systems Active</span>
    </div>
    <p>Local-first deterministic AI workflow daemon unifying M1 Learning & Observation with M2 Execution & Safety.</p>
    <div class="links">
      <a class="link-item" href="/docs">
        <div>
          <strong>Swagger API Documentation</strong>
          <div style="font-size: 13px; color: #94a3b8;">Interactive REST API Explorer</div>
        </div>
        <span>/docs &rarr;</span>
      </a>
      <a class="link-item" href="http://127.0.0.1:7701" target="_blank">
        <div>
          <strong>Acme Portal (Demo App)</strong>
          <div style="font-size: 13px; color: #94a3b8;">Synthetic Customer Self-Service Portal</div>
        </div>
        <span>Port 7701 &rarr;</span>
      </a>
      <a class="link-item" href="http://127.0.0.1:7702" target="_blank">
        <div>
          <strong>Acme CRM (Demo App)</strong>
          <div style="font-size: 13px; color: #94a3b8;">Synthetic Back-office Support Desk</div>
        </div>
        <span>Port 7702 &rarr;</span>
      </a>
      <a class="link-item" href="/notch" target="_blank">
        <div>
          <strong>DigitalSoul Notch UI (Web View)</strong>
          <div style="font-size: 13px; color: #94a3b8;">Workflow Recording & Step Review UI</div>
        </div>
        <span>/notch &rarr;</span>
      </a>
      <a class="link-item" href="/health">
        <div>
          <strong>Health Endpoint</strong>
          <div style="font-size: 13px; color: #94a3b8;">JSON readiness check</div>
        </div>
        <span>/health &rarr;</span>
      </a>
    </div>
  </div>
</body>
</html>"""


@app.get("/health")
def health_check():
    """Health check for agent readiness."""
    return {
        "status": "healthy",
        "agent": "DigitalSoul",
        "version": "1.1.0",
        "roles": {
            "m1_learning": "active",
            "m2_execution": "active",
        },
    }


# Include M1 Learning routes
app.include_router(learn_router)

# Include M2 Execution & Run routes
app.include_router(run_router(runtime))

# Mount Notch Desktop UI static files
_notch_ui_dir = Path(__file__).resolve().parents[2] / "desktop" / "ui"
if _notch_ui_dir.is_dir():
    app.mount("/notch", StaticFiles(directory=str(_notch_ui_dir), html=True), name="notch")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent.ghost.app:app", host="127.0.0.1", port=7777, reload=True)

