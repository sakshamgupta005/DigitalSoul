"""DigitalSoul Local Agent (Loopback FastAPI Service).

Binds strictly to 127.0.0.1:7777.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from agent.ghost.api.learn_routes import router as learn_router


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


@app.get("/health")
def health_check():
    """Health check for agent readiness."""
    return {
        "status": "healthy",
        "agent": "DigitalSoul",
        "version": "1.1.0",
        "role_m1": "learning_and_intelligence_active",
    }


# Include Developer 1 Learning routes
app.include_router(learn_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent.ghost.app:app", host="127.0.0.1", port=7777, reload=True)
