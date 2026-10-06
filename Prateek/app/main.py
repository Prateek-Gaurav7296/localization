"""FastAPI application entry point: ``uvicorn app.main:app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.routes import extraction, health

# Multipart framing overhead allowed on top of MAX_UPLOAD_SIZE_MB before the early 413 check trips.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

logger = logging.getLogger("app")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("app").setLevel(level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.input_root.mkdir(parents=True, exist_ok=True)
    for name, binary in (("ffmpeg", settings.ffmpeg_binary), ("ffprobe", settings.ffprobe_binary)):
        tool = health.tool_status(binary)
        if tool.available:
            logger.info("%s available: %s", name, tool.version)
        else:
            logger.error("%s binary %r not found or not runnable; /extract will fail", name, binary)
    logger.info("input root: %s (max upload %d MB)", settings.input_root, settings.max_upload_size_mb)
    yield


app = FastAPI(
    title="Input-state video/audio extraction",
    version=__version__,
    description="Split an uploaded video into a silent MP4 and its complete audio track.",
    lifespan=lifespan,
)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    """Refuse uploads whose declared size is too large before the body is read."""
    if request.method == "POST" and request.url.path == "/extract":
        declared = request.headers.get("content-length")
        settings = get_settings()
        if (
            declared
            and declared.isdigit()
            and int(declared) > settings.max_upload_size_bytes + MULTIPART_OVERHEAD_BYTES
        ):
            detail = f"Uploaded file exceeds the maximum allowed size of {settings.max_upload_size_mb} MB"
            return JSONResponse(status_code=413, content={"detail": detail})
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health.router)
app.include_router(extraction.router)
