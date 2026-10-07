"""Data passed between pipeline stages and returned to callers (UI, scripts, tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config.languages import Language

if TYPE_CHECKING:
    from app.pipeline.intermediate.contract import IntermediateResult


class Stage(StrEnum):
    INPUT = "input"
    STT = "stt"
    TRANSLATION = "translation"
    TTS = "tts"
    VIDEO = "video"
    OUTPUT = "output"

    @property
    def label(self) -> str:
        return _LABELS[self][0]

    @property
    def failure_label(self) -> str:
        return _LABELS[self][1]


_LABELS = {
    Stage.INPUT: ("Input Processing", "Input processing failed"),
    Stage.STT: ("Speech-to-Text", "STT failed"),
    Stage.TRANSLATION: ("Translation / Transformation", "Translation failed"),
    Stage.TTS: ("Text-to-Speech", "TTS failed"),
    Stage.VIDEO: ("Video Localization", "Video generation failed"),
    Stage.OUTPUT: ("Output Generation", "Output generation failed"),
}


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class InputResult:
    """Result of the input stage: the saved upload split into a silent video and its audio."""

    run_id: str
    run_dir: Path
    original_video: Path
    silent_video: Path  # video.mp4, no audio stream
    source_audio: Path  # audio.m4a, every audio track of the original
    video_duration_seconds: float | None
    audio_duration_seconds: float | None
    audio_track_count: int
    metadata: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class OutputResult:
    """Result of the output stage: the lip-synced, localized video."""

    localized_video: Path
    provider: str  # "sync" or "mock"
    generation_id: str | None
    duration_seconds: float | None = None
    has_audio: bool = True


@dataclass
class LocalizationResult:
    run_id: str | None
    run_dir: Path | None
    target_language: Language
    source_language: Language | None  # None means "auto-detect" was requested
    engine: str
    lipsync_provider: str
    stages: dict[Stage, StageStatus] = field(default_factory=dict)
    stage_seconds: dict[Stage, float] = field(default_factory=dict)
    input: InputResult | None = None
    intermediate: IntermediateResult | None = None
    output: OutputResult | None = None
    manifest_path: Path | None = None
