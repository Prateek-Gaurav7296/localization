"""API response models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class OutputKind(StrEnum):
    video = "video"
    audio = "audio"


class ExtractionResponse(BaseModel):
    status: str = Field(examples=["success"])
    run_id: str = Field(examples=["sample_video__20261004_103015_123456"])
    input_file: str = Field(description="Saved original upload, relative to the input root's parent")
    video_file: str = Field(description="Video-only MP4, relative to the input root's parent")
    audio_file: str = Field(description="Extracted audio (.m4a), relative to the input root's parent")
    metadata_file: str
    video_download_url: str
    audio_download_url: str
    run_url: str
    video_duration_seconds: float | None
    audio_duration_seconds: float | None
    audio_track_count: int
    video_strategy: str = Field(description="'copy' (stream copied) or 'reencode' (H.264)")
    audio_strategy: str = Field(description="'copy', 'mixed' (some tracks copied) or 'encode' (AAC)")


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])


class ToolStatus(BaseModel):
    binary: str
    available: bool
    version: str | None = None


class DependencyHealthResponse(BaseModel):
    status: str = Field(examples=["ok", "degraded"])
    ffmpeg: ToolStatus
    ffprobe: ToolStatus


class ErrorResponse(BaseModel):
    detail: str
