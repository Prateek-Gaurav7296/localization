"""End-to-end orchestration: Input -> STT -> TTT -> TTS -> lip-sync -> output.

This is the only entry point the UI (or any other caller) needs. Stage implementations live in
``app.pipeline.input``, ``app.pipeline.intermediate`` and ``app.pipeline.output``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

from app.config import Settings, get_language, get_settings
from app.config.languages import Language
from app.pipeline.errors import EngineError, PipelineError
from app.pipeline.input import process_input
from app.pipeline.input.errors import ExtractionError
from app.pipeline.input.extraction_service import RunLogger
from app.pipeline.input.media_service import probe
from app.pipeline.intermediate import (
    IntermediateRequest,
    IntermediateResult,
    LocalizationEngine,
    SynthesizedAudio,
    Translation,
    get_engine,
)
from app.pipeline.models import LocalizationResult, OutputResult, Stage, StageStatus
from app.pipeline.output import LipSyncClientFactory, generate_localized_output
from app.pipeline.output.media_files import MediaFileError
from app.pipeline.output.sync_service import SyncError

logger = logging.getLogger("app.pipeline")

ProgressCallback = Callable[[Stage, StageStatus, str | None], None]
T = TypeVar("T")

MANIFEST_NAME = "localization.json"
INTERMEDIATE_DIR = "intermediate"
LOCALIZED_DIR = "localized"


def localize_video(
    source: BinaryIO | Path,
    target_language: str,
    source_language: str | None = None,
    *,
    filename: str | None = None,
    settings: Settings | None = None,
    engine: LocalizationEngine | None = None,
    lipsync_client_factory: LipSyncClientFactory | None = None,
    on_progress: ProgressCallback | None = None,
) -> LocalizationResult:
    """Localize one ad video into ``target_language`` (an ISO 639-1 code such as ``"hi"``).

    ``source`` is a path or a binary file object (then ``filename`` is required, e.g. an upload).
    ``source_language=None`` lets the STT engine detect the language.
    ``engine`` / ``lipsync_client_factory`` override the configured implementations (tests, scripts).

    Returns a ``LocalizationResult``; raises ``PipelineError`` naming the failed stage, with the
    partial result attached as ``error.result``.
    """
    settings = settings or get_settings()
    target = _language(target_language, "target")
    source_lang = _language(source_language, "source") if source_language else None
    if source_lang == target:
        raise PipelineError(Stage.INPUT, f"Source and target language are both {target.name}")
    engine = engine or get_engine(settings)

    result = LocalizationResult(
        run_id=None,
        run_dir=None,
        target_language=target,
        source_language=source_lang,
        engine=engine.name,
        lipsync_provider=settings.pipeline.lipsync_provider,
        stages=dict.fromkeys(Stage, StageStatus.PENDING),
    )
    run = _Run(result, on_progress)

    # 1. Input: store the upload and split it into silent video + audio.
    if isinstance(source, Path):
        with source.open("rb") as fh:
            inp = run.stage(Stage.INPUT, process_input, fh, filename or source.name, settings.input)
    else:
        if not filename:
            raise PipelineError(Stage.INPUT, "A filename is required when passing a file object")
        inp = run.stage(Stage.INPUT, process_input, source, filename, settings.input)
    result.input, result.run_id, result.run_dir = inp, inp.run_id, inp.run_dir
    run.log = RunLogger(logger, {"run_id": inp.run_id})
    run.log.info("localizing to %s with engine=%s lipsync=%s", target.code, engine.name, result.lipsync_provider)

    # 2-4. Intermediate: STT -> TTT -> TTS.
    work_dir = inp.run_dir / INTERMEDIATE_DIR
    work_dir.mkdir(exist_ok=True)
    request = IntermediateRequest(
        run_id=inp.run_id,
        work_dir=work_dir,
        source_audio=inp.source_audio,
        silent_video=inp.silent_video,
        source_language=source_lang,
        target_language=target,
        audio_duration_seconds=inp.audio_duration_seconds,
    )
    transcript = run.stage(Stage.STT, engine.transcribe, request)
    _write_json(work_dir / "transcript.json", dataclasses.asdict(transcript))
    translation = run.stage(Stage.TRANSLATION, engine.translate, transcript, request)
    _write_json(work_dir / "translation.json", dataclasses.asdict(translation))
    audio = run.stage(Stage.TTS, _synthesize, engine, translation, request)
    result.intermediate = IntermediateResult(transcript, translation, audio)

    # 5. Video localization: lip-sync the silent video to the new audio.
    output = run.stage(
        Stage.VIDEO,
        generate_localized_output,
        inp.silent_video,
        audio.path,
        inp.run_dir / LOCALIZED_DIR,
        settings=settings,
        client_factory=lipsync_client_factory,
    )

    # 6. Output generation: verify the final file and record the run.
    result.output = run.stage(Stage.OUTPUT, _verify_output, output, settings)
    run.write_manifest("success")
    run.log.info("localization completed in %.1fs", sum(result.stage_seconds.values()))
    return result


class _Run:
    """Tracks stage status/timing, reports progress and turns failures into PipelineError."""

    def __init__(self, result: LocalizationResult, on_progress: ProgressCallback | None) -> None:
        self.result = result
        self.on_progress = on_progress
        self.log: logging.Logger | logging.LoggerAdapter = logger
        self.started_at = datetime.now().astimezone()

    def stage(self, stage: Stage, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        self._set(stage, StageStatus.RUNNING, None)
        started = time.monotonic()
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:
            self.result.stage_seconds[stage] = round(time.monotonic() - started, 3)
            message, expected = _describe(exc)
            if expected:
                self.log.error("%s: %s", stage.failure_label, message)
            else:
                self.log.exception("%s with an unexpected error", stage.failure_label)
            self._set(stage, StageStatus.FAILED, message)
            self.write_manifest("failed", error={"stage": stage.value, "message": message})
            raise PipelineError(stage, message, self.result) from exc
        self.result.stage_seconds[stage] = round(time.monotonic() - started, 3)
        self._set(stage, StageStatus.COMPLETED, None)
        return value

    def _set(self, stage: Stage, status: StageStatus, detail: str | None) -> None:
        self.result.stages[stage] = status
        if self.on_progress:
            self.on_progress(stage, status, detail)

    def write_manifest(self, status: str, error: dict[str, str] | None = None) -> None:
        r = self.result
        if r.run_dir is None:  # input stage failed before a run existed; metadata.json covers it
            return
        rel = _relative_to(r.run_dir)
        transcript = r.intermediate.transcript if r.intermediate else None
        manifest = {
            "run_id": r.run_id,
            "status": status,
            "started_at": self.started_at.isoformat(),
            "finished_at": datetime.now().astimezone().isoformat(),
            "source_language": r.source_language.code if r.source_language else "auto",
            "detected_source_language": transcript.language if transcript else None,
            "target_language": r.target_language.code,
            "engine": r.engine,
            "lipsync_provider": r.lipsync_provider,
            "stages": {s.value: {"status": r.stages[s].value, "seconds": r.stage_seconds.get(s)} for s in Stage},
            "artifacts": {
                "original_video": rel(r.input.original_video) if r.input else None,
                "silent_video": rel(r.input.silent_video) if r.input else None,
                "source_audio": rel(r.input.source_audio) if r.input else None,
                "localized_audio": rel(r.intermediate.audio.path) if r.intermediate else None,
                "localized_video": rel(r.output.localized_video) if r.output else None,
            },
            "transcript": transcript.text if transcript else None,
            "translation": r.intermediate.translation.text if r.intermediate else None,
            "generation_id": r.output.generation_id if r.output else None,
            "localized_video_duration_seconds": r.output.duration_seconds if r.output else None,
            "error": error,
        }
        r.manifest_path = r.run_dir / MANIFEST_NAME
        _write_json(r.manifest_path, manifest)


def _synthesize(engine: LocalizationEngine, translation: Translation, request: IntermediateRequest) -> SynthesizedAudio:
    audio = engine.synthesize(translation, request)
    if not audio.path.is_file() or audio.path.stat().st_size == 0:
        raise EngineError(f"TTS reported {audio.path.name} but the file is missing or empty")
    return audio


def _verify_output(output: OutputResult, settings: Settings) -> OutputResult:
    if not output.localized_video.is_file():
        raise RuntimeError(f"Localized video {output.localized_video} was not created")
    info = probe(output.localized_video, settings.input.ffprobe_binary)
    if not info.video_streams:
        raise RuntimeError("Localized video has no video stream")
    if not info.audio_streams:
        raise RuntimeError("Localized video has no audio stream")
    return dataclasses.replace(output, duration_seconds=info.duration, has_audio=True)


def _describe(exc: Exception) -> tuple[str, bool]:
    """Operator-facing message for an exception, and whether it is an expected (handled) failure."""
    if isinstance(exc, ExtractionError):
        return exc.message, True
    if isinstance(exc, (SyncError, MediaFileError)):
        return exc.message, True
    if isinstance(exc, EngineError):
        return str(exc), True
    return f"{type(exc).__name__}: {exc}", False


def _language(code: str, role: str) -> Language:
    try:
        return get_language(code)
    except ValueError as exc:
        raise PipelineError(Stage.INPUT, f"Invalid {role} language: {exc}") from exc


def _relative_to(base: Path) -> Callable[[Path], str]:
    def rel(path: Path) -> str:
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return str(path)

    return rel


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
