"""Input stage: save the uploaded ad video and split it into a silent video and its audio."""

from __future__ import annotations

from typing import BinaryIO

from app.config import InputSettings
from app.pipeline.input import extraction_service
from app.pipeline.models import InputResult


def process_input(source: BinaryIO, filename: str, settings: InputSettings) -> InputResult:
    """Validate, store and split one video. Raises ``ExtractionError`` subclasses on failure.

    Creates ``<runs_root>/<name>/<timestamp>/`` with ``input/original_video.<ext>``,
    ``output/video.mp4`` (no audio), ``output/audio.m4a`` (all audio tracks) and ``metadata.json``.
    """
    outcome = extraction_service.process_upload(source, filename, settings)
    meta = outcome.metadata
    return InputResult(
        run_id=outcome.run.run_id,
        run_dir=outcome.run.run_dir,
        original_video=outcome.original_input,
        silent_video=outcome.run.video_output,
        source_audio=outcome.run.audio_output,
        video_duration_seconds=meta.get("video_duration_seconds"),
        audio_duration_seconds=meta.get("audio_duration_seconds"),
        audio_track_count=meta.get("audio_track_count", 0),
        metadata=meta,
    )


__all__ = ["process_input"]
