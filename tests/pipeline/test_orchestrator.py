"""End-to-end orchestration: real input stage, engine contract, real SyncService against a fake server."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.pipeline import EngineError, PipelineError, Stage, StageStatus, localize_video
from app.pipeline.intermediate import (
    IntermediateRequest,
    LocalizationEngine,
    Segment,
    SynthesizedAudio,
    Transcript,
    Translation,
)
from tests.helpers import ffmpeg, probe_json, requires_ffmpeg, stream_indexes
from tests.pipeline.conftest import FakeSyncServer

pytestmark = requires_ffmpeg


class RecordingEngine(LocalizationEngine):
    """A stand-in for a real STT/TTT/TTS engine, built only on the public contract."""

    name = "recording"

    def __init__(self, fail_at: str | None = None):
        self.fail_at = fail_at
        self.requests: list[IntermediateRequest] = []

    def transcribe(self, request):
        self.requests.append(request)
        self._maybe_fail("transcribe")
        return Transcript(
            language="en", text="Buy one get one free", segments=(Segment(0.0, 1.5, "Buy one get one free"),)
        )

    def translate(self, transcript, request):
        self._maybe_fail("translate")
        return Translation(transcript.language, request.target_language.code, "एक खरीदें एक मुफ्त पाएं")

    def synthesize(self, translation, request):
        self._maybe_fail("synthesize")
        out = request.work_dir / "tts.wav"
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(out))
        return SynthesizedAudio(path=out, duration_seconds=3.0)

    def _maybe_fail(self, step: str) -> None:
        if self.fail_at == step:
            raise EngineError(f"{step} exploded")


def test_full_pipeline_success(make_settings, samples):
    fake = FakeSyncServer(samples["sample.mp4"])
    engine = RecordingEngine()
    events: list[tuple[Stage, StageStatus]] = []

    result = localize_video(
        samples["sample.mp4"], "hi", "en",
        settings=make_settings(), engine=engine, lipsync_client_factory=fake.client_factory,
        on_progress=lambda stage, status, detail: events.append((stage, status)),
    )  # fmt: skip

    # Every stage ran, in order, and reported running -> completed.
    expected = [(s, st) for s in Stage for st in (StageStatus.RUNNING, StageStatus.COMPLETED)]
    assert events == expected
    assert all(status is StageStatus.COMPLETED for status in result.stages.values())

    # Input stage artifacts (Prateek's flow) feed the engine.
    request = engine.requests[0]
    assert request.source_audio == result.input.source_audio
    assert request.target_language.code == "hi" and request.source_language.code == "en"
    assert request.work_dir == result.run_dir / "intermediate"

    # Output stage (Swati's SyncService) received the silent video and the TTS audio.
    assert set(fake.uploads) == {"video", "audio"}
    assert fake.uploads["video"] == result.input.silent_video.read_bytes()
    assert fake.uploads["audio"] == (result.run_dir / "intermediate" / "tts.wav").read_bytes()
    assert stream_indexes(result.input.silent_video, "a") == []

    # Final output is verified and recorded.
    video = result.output.localized_video
    assert video.parent == result.run_dir / "localized" and video.is_file()
    assert result.output.generation_id == "gen-1"
    assert result.output.duration_seconds == pytest.approx(3, abs=0.2)

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["status"] == "success"
    assert manifest["target_language"] == "hi"
    assert manifest["translation"] == "एक खरीदें एक मुफ्त पाएं"
    assert manifest["artifacts"]["localized_video"] == "localized/gen-1.mp4"
    assert all(s["status"] == "completed" for s in manifest["stages"].values())
    transcript = json.loads((result.run_dir / "intermediate" / "transcript.json").read_text())
    assert transcript["segments"][0]["text"] == "Buy one get one free"


def test_file_object_source_and_auto_detect(make_settings, samples):
    fake = FakeSyncServer(samples["sample.mp4"])
    data = io.BytesIO(samples["sample.mov"].read_bytes())
    result = localize_video(
        data, "ta", filename="My Ad.mov",
        settings=make_settings(), engine=RecordingEngine(), lipsync_client_factory=fake.client_factory,
    )  # fmt: skip
    assert result.source_language is None
    assert result.run_id.startswith("My_Ad__")
    assert result.input.original_video.name == "original_video.mov"


@pytest.mark.parametrize(
    ("fail_at", "stage", "message"),
    [
        ("transcribe", Stage.STT, "STT failed: transcribe exploded"),
        ("translate", Stage.TRANSLATION, "Translation failed: translate exploded"),
        ("synthesize", Stage.TTS, "TTS failed: synthesize exploded"),
    ],
)
def test_engine_failures_name_the_stage(make_settings, samples, fail_at, stage, message):
    with pytest.raises(PipelineError) as info:
        localize_video(samples["sample.mp4"], "hi", settings=make_settings(), engine=RecordingEngine(fail_at))
    error = info.value
    assert error.stage is stage
    assert str(error) == message
    assert isinstance(error.__cause__, EngineError)
    result = error.result
    assert result.stages[Stage.INPUT] is StageStatus.COMPLETED
    assert result.stages[stage] is StageStatus.FAILED
    assert result.stages[Stage.VIDEO] is StageStatus.PENDING
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"] == {"stage": stage.value, "message": message.split(": ", 1)[1]}


def test_tts_returning_missing_file_fails_tts(make_settings, samples):
    class NoFileEngine(RecordingEngine):
        def synthesize(self, translation, request):
            return SynthesizedAudio(path=request.work_dir / "nothing.wav")

    with pytest.raises(PipelineError, match=r"^TTS failed: TTS reported nothing.wav but the file is missing"):
        localize_video(samples["sample.mp4"], "hi", settings=make_settings(), engine=NoFileEngine())


def test_ishnit_engine_is_a_clear_not_integrated_error(make_settings, samples):
    with pytest.raises(PipelineError) as info:
        localize_video(samples["sample.mp4"], "hi", settings=make_settings(engine="ishnit"))
    assert info.value.stage is Stage.STT
    assert "not integrated yet" in str(info.value)


@pytest.mark.parametrize(
    ("sample", "message"),
    [
        ("no_audio.mp4", "Input processing failed: Uploaded video has no audio stream"),
        ("corrupt.mp4", "Input processing failed: Uploaded file is not a valid or readable video file"),
    ],
)
def test_input_failures(make_settings, samples, sample, message):
    with pytest.raises(PipelineError, match=f"^{message}") as info:
        localize_video(samples[sample], "hi", settings=make_settings(), engine=RecordingEngine())
    assert info.value.stage is Stage.INPUT


def test_unsupported_extension_rejected_at_input(make_settings, tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    with pytest.raises(PipelineError, match="^Input processing failed: Unsupported video format"):
        localize_video(text, "hi", settings=make_settings(), engine=RecordingEngine())


@pytest.mark.parametrize(("source", "target"), [("en", "xx"), ("zz", "hi"), ("hi", "hi")])
def test_language_validation(make_settings, samples, source, target):
    with pytest.raises(PipelineError) as info:
        localize_video(samples["sample.mp4"], target, source, settings=make_settings(), engine=RecordingEngine())
    assert info.value.stage is Stage.INPUT
    assert not (make_settings().input.runs_root).exists()  # rejected before any work


def test_sync_generation_failure_is_video_stage(make_settings, samples):
    fake = FakeSyncServer(samples["sample.mp4"], final_status="FAILED", error="No face detected")
    with pytest.raises(PipelineError) as info:
        localize_video(
            samples["sample.mp4"], "hi", settings=make_settings(), engine=RecordingEngine(),
            lipsync_client_factory=fake.client_factory,
        )  # fmt: skip
    assert str(info.value) == "Video generation failed: Sync generation failed: No face detected"
    assert info.value.result.intermediate is not None  # TTS audio is still available


def test_missing_sync_api_key(make_settings, samples):
    with pytest.raises(PipelineError, match="^Video generation failed: SYNC_API_KEY is not configured"):
        localize_video(samples["sample.mp4"], "hi", settings=make_settings(sync_api_key=""), engine=RecordingEngine())


def test_mock_engine_and_mock_lipsync_run_end_to_end(make_settings, samples):
    result = localize_video(samples["sample.mkv"], "ml", settings=make_settings(engine="mock", lipsync="mock"))
    assert result.engine == "mock" and result.lipsync_provider == "mock"
    assert result.intermediate.transcript.text.startswith("[MOCK")
    video = result.output.localized_video
    assert stream_indexes(video, "v") and len(stream_indexes(video, "a")) == 1
    assert float(probe_json(video)["format"]["duration"]) == pytest.approx(3, abs=0.2)


def test_output_stage_ignores_engine_internals(make_settings, samples, tmp_path: Path):
    """Swapping engines needs no change elsewhere: the same orchestrator call works for any engine."""
    for engine in (RecordingEngine(), None):  # custom engine, then the configured (mock) engine
        fake = FakeSyncServer(samples["sample.mp4"])
        result = localize_video(
            samples["sample.mp4"], "bn", settings=make_settings(), engine=engine,
            lipsync_client_factory=fake.client_factory,
        )  # fmt: skip
        assert result.output.localized_video.is_file()
