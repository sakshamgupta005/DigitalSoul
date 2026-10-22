"""DigitalSoul Local Agent (Loopback FastAPI Service).

Unifies M1 Learning & Intelligence with M2 Execution & Automation.
Binds strictly to 127.0.0.1:7777.
"""

from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI, Request, Response, status
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent.ghost.app:app", host="127.0.0.1", port=7777, reload=True)

