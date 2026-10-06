"""Liveness and dependency checks."""

from __future__ import annotations

import shutil
import subprocess
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.config import Settings, get_settings
from app.schemas.extraction import DependencyHealthResponse, HealthResponse, ToolStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/dependencies", response_model=DependencyHealthResponse)
def dependency_health(
    response: Response, settings: Annotated[Settings, Depends(get_settings)]
) -> DependencyHealthResponse:
    """Report whether ffmpeg/ffprobe are installed and runnable. Returns 503 if either is missing."""
    ffmpeg = tool_status(settings.ffmpeg_binary)
    ffprobe = tool_status(settings.ffprobe_binary)
    healthy = ffmpeg.available and ffprobe.available
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return DependencyHealthResponse(status="ok" if healthy else "degraded", ffmpeg=ffmpeg, ffprobe=ffprobe)


def tool_status(binary: str) -> ToolStatus:
    if shutil.which(binary) is None:
        return ToolStatus(binary=binary, available=False)
    try:
        result = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ToolStatus(binary=binary, available=False)
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else None
    return ToolStatus(binary=binary, available=result.returncode == 0, version=first_line)
