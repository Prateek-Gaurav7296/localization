"""FFprobe-based stream inspection and validation."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.errors import InvalidMediaError, NoAudioStreamError, ProcessingError

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str | None
    duration: float | None
    is_attached_picture: bool = False
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None
    language: str | None = None

    def summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None and v is not False}


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    format_name: str | None
    format_long_name: str | None
    duration: float | None
    size_bytes: int | None
    bit_rate: int | None
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def video_streams(self) -> list[StreamInfo]:
        """Real video streams; embedded cover art (attached pictures) is excluded."""
        return [s for s in self.streams if s.codec_type == "video" and not s.is_attached_picture]

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def primary_video(self) -> StreamInfo:
        return self.video_streams[0]

    def audio_duration(self) -> float | None:
        """Longest known audio stream duration (None if no stream reports one)."""
        durations = [s.duration for s in self.audio_streams if s.duration]
        return max(durations) if durations else None

    def video_duration(self) -> float | None:
        if self.video_streams and self.primary_video.duration:
            return self.primary_video.duration
        return self.duration

    def summary(self) -> dict[str, Any]:
        return {
            "format_name": self.format_name,
            "format_long_name": self.format_long_name,
            "duration_seconds": self.duration,
            "size_bytes": self.size_bytes,
            "bit_rate": self.bit_rate,
            "streams": [s.summary() for s in self.streams],
        }


def probe(path: Path, ffprobe_binary: str) -> MediaInfo:
    """Run ffprobe and parse its JSON. Raises InvalidMediaError if the file is unreadable."""
    cmd = [
        ffprobe_binary,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except FileNotFoundError as exc:
        raise ProcessingError(
            "Media inspection tool is unavailable", detail=f"ffprobe not found: {ffprobe_binary}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise InvalidMediaError("Timed out while inspecting the uploaded file", detail=str(exc)) from exc

    if result.returncode != 0:
        raise InvalidMediaError(
            "Uploaded file is not a valid or readable video file",
            detail=f"ffprobe exit {result.returncode}: {result.stderr.strip()[-2000:]}",
        )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidMediaError("Uploaded file is not a valid or readable video file", detail=str(exc)) from exc

    fmt = data.get("format") or {}
    streams = [_parse_stream(s) for s in data.get("streams") or []]
    if not fmt or not streams:
        raise InvalidMediaError(
            "Uploaded file is not a valid or readable video file",
            detail=f"ffprobe found format={bool(fmt)} streams={len(streams)}",
        )
    return MediaInfo(
        path=path,
        format_name=fmt.get("format_name"),
        format_long_name=fmt.get("format_long_name"),
        duration=_to_float(fmt.get("duration")),
        size_bytes=_to_int(fmt.get("size")),
        bit_rate=_to_int(fmt.get("bit_rate")),
        streams=streams,
    )


def validate_input(info: MediaInfo) -> None:
    """Ensure the input has a decodable video stream and at least one audio stream."""
    if not info.video_streams:
        raise InvalidMediaError("Uploaded file contains no video stream")
    if not info.audio_streams:
        raise NoAudioStreamError("Uploaded video has no audio stream, so there is no audio to extract")


def _parse_stream(raw: dict[str, Any]) -> StreamInfo:
    tags = {str(k).lower(): v for k, v in (raw.get("tags") or {}).items()}
    duration = _to_float(raw.get("duration"))
    if duration is None:
        # Matroska stores per-stream duration only as a tag, e.g. "00:00:02.000000000".
        duration = _parse_clock(tags.get("duration"))
    return StreamInfo(
        index=int(raw.get("index", 0)),
        codec_type=str(raw.get("codec_type", "unknown")),
        codec_name=raw.get("codec_name"),
        duration=duration,
        is_attached_picture=bool((raw.get("disposition") or {}).get("attached_pic")),
        width=_to_int(raw.get("width")),
        height=_to_int(raw.get("height")),
        channels=_to_int(raw.get("channels")),
        channel_layout=raw.get("channel_layout"),
        sample_rate=_to_int(raw.get("sample_rate")),
        bit_rate=_to_int(raw.get("bit_rate")),
        language=tags.get("language"),
    )


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_clock(value: Any) -> float | None:
    if not isinstance(value, str) or value.count(":") != 2:
        return None
    try:
        hours, minutes, seconds = value.split(":")
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return total if total > 0 else None
