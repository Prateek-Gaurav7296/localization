"""End-to-end pipeline: save upload -> probe -> extract video -> extract audio -> metadata."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from app.config import Settings
from app.errors import ExtractionError, ProcessingError
from app.services import ffmpeg_service, file_service, media_service
from app.services.file_service import RunPaths

logger = logging.getLogger("app.extraction")


class RunLogger(logging.LoggerAdapter):
    """Prefixes every message with the run_id so a request can be traced through the logs."""

    def process(self, msg, kwargs):
        return f"[run_id={self.extra['run_id']}] {msg}", kwargs


@dataclass(frozen=True)
class ExtractionOutcome:
    run: RunPaths
    original_input: Path
    metadata: dict[str, Any]


def process_upload(source: BinaryIO, filename: str, settings: Settings) -> ExtractionOutcome:
    """Run the whole pipeline for one uploaded file.

    Raises an ExtractionError subclass on failure. If a run directory was created, its
    metadata.json records the failure and any partial outputs are removed.
    """
    extension = file_service.validate_extension(filename)
    settings.input_root.mkdir(parents=True, exist_ok=True)
    run = file_service.create_run(settings.input_root, filename)
    log = RunLogger(logger, {"run_id": run.run_id})
    started = time.monotonic()
    log.info("run started: filename=%r dir=%s", file_service.client_basename(filename), run.run_dir)

    metadata: dict[str, Any] = {
        "run_id": run.run_id,
        "input_filename": file_service.client_basename(filename),
        "sanitized_name": run.name,
        "input_format": extension,
        "timestamp": run.timestamp.isoformat(),
        "input_file": None,
        "video_output": None,
        "audio_output": None,
        "processing_status": "processing",
    }
    original = run.original_input(extension)

    try:
        size = file_service.save_upload(source, original, settings.max_upload_size_bytes)
        metadata["input_file"] = _rel(original, run.run_dir)
        metadata["input_size_bytes"] = size
        log.info("input received: %d bytes saved to %s", size, original.name)

        source_info = media_service.probe(original, settings.ffprobe_binary)
        metadata["input_media"] = source_info.summary()
        log.info(
            "ffprobe completed: format=%s video_streams=%d audio_streams=%d duration=%s",
            source_info.format_name, len(source_info.video_streams),
            len(source_info.audio_streams), source_info.duration,
        )  # fmt: skip
        media_service.validate_input(source_info)
        log.info("input validated")

        common = {
            "ffmpeg_binary": settings.ffmpeg_binary,
            "ffprobe_binary": settings.ffprobe_binary,
            "timeout": settings.ffmpeg_timeout_seconds,
            "log": log,
        }
        log.info("video extraction started")
        video = ffmpeg_service.extract_video(source_info, run.video_output, **common)
        log.info("video extraction finished: strategy=%s", video.strategy)

        log.info("audio extraction started")
        audio = ffmpeg_service.extract_audio(source_info, run.audio_output, **common)
        log.info("audio extraction finished: strategy=%s tracks=%d", audio.strategy, len(audio.info.audio_streams))

        metadata.update(
            {
                "video_output": _rel(run.video_output, run.run_dir),
                "audio_output": _rel(run.audio_output, run.run_dir),
                "video_duration_seconds": _round(video.info.video_duration()),
                "audio_duration_seconds": _round(audio.info.audio_duration() or audio.info.duration),
                "input_duration_seconds": _round(source_info.duration),
                "audio_track_count": len(audio.info.audio_streams),
                "video_strategy": video.strategy,
                "audio_strategy": audio.strategy,
                "video_output_media": video.info.summary(),
                "audio_output_media": audio.info.summary(),
                "ffmpeg_commands": {
                    "video": _display_command(video.command, run.run_dir),
                    "audio": _display_command(audio.command, run.run_dir),
                },
                "processing_status": "success",
            }
        )
    except ExtractionError as exc:
        _record_failure(run, metadata, exc, log)
        raise
    except Exception as exc:
        wrapped = ProcessingError("Unexpected error while processing the video", detail=repr(exc))
        log.exception("processing failed with an unexpected error")
        _record_failure(run, metadata, wrapped, log)
        raise wrapped from exc
    finally:
        metadata["completed_at"] = datetime.now().astimezone().isoformat()
        metadata["processing_time_seconds"] = round(time.monotonic() - started, 3)
        file_service.write_metadata(run.metadata_file, metadata)

    log.info(
        "processing completed in %.2fs: video=%ss audio=%ss",
        metadata["processing_time_seconds"], metadata["video_duration_seconds"], metadata["audio_duration_seconds"],
    )  # fmt: skip
    return ExtractionOutcome(run=run, original_input=original, metadata=metadata)


def _record_failure(run: RunPaths, metadata: dict[str, Any], exc: ExtractionError, log: logging.LoggerAdapter) -> None:
    file_service.clear_directory(run.output_dir)
    metadata.update(
        {
            "processing_status": "failed",
            "video_output": None,
            "audio_output": None,
            "error": {
                "type": type(exc).__name__,
                "stage": exc.stage,
                "http_status": exc.status_code,
                "message": exc.message,
                "detail": exc.detail,
            },
        }
    )
    level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    log.log(level, "processing failed at stage=%s: %s | %s", exc.stage, exc.message, exc.detail)


def _rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _display_command(cmd: list[str], run_dir: Path) -> list[str]:
    """Show paths relative to the run directory so metadata doesn't leak absolute server paths."""
    prefix = str(run_dir) + "/"
    return [arg.replace(prefix, "") for arg in cmd]


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
