"""Convenience launcher: ``python run.py`` (honours HOST, PORT, RELOAD env vars)."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),  # noqa: S104 - binding all interfaces is intended for a server
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "").lower() in {"1", "true", "yes"},
    )
