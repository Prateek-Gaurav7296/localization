"""Stage interfaces used on their own: input, output, engine selection, languages."""

from __future__ import annotations

import pytest

from app.config import LANGUAGES, get_language
from app.config.settings import PROJECT_ROOT
from app.dev.mock_engine import MockLocalizationEngine
from app.pipeline.input import process_input
from app.pipeline.intermediate import LocalizationEngine, get_engine
from app.pipeline.intermediate.ishnit_engine import IshnitEngine
from app.pipeline.output import generate_localized_output
from app.pipeline.output.media_files import MAX_FILE_BYTES, MediaFileError, load_media_file
from tests.helpers import requires_ffmpeg, stream_indexes
from tests.pipeline.conftest import FakeSyncServer

SAMPLE_VIDEO = PROJECT_ROOT / "samples" / "output_stage" / "Input_Video_For_Sync.mov"
SAMPLE_AUDIO = PROJECT_ROOT / "samples" / "output_stage" / "Input_Malayam_Audio_For_Sync.mp3"


@requires_ffmpeg
def test_process_input_returns_stage_artifacts(make_settings, samples):
    settings = make_settings().input
    with samples["sample.mkv"].open("rb") as fh:
        result = process_input(fh, "campaign.mkv", settings)
    assert result.run_dir.parent.parent == settings.runs_root
    assert result.silent_video.name == "video.mp4" and stream_indexes(result.silent_video, "a") == []
    assert result.source_audio.name == "audio.m4a" and result.audio_track_count == 2
    assert result.metadata["processing_status"] == "success"


def test_output_stage_runs_standalone_on_swati_samples(make_settings, tmp_path, samples):
    """The output stage needs no UI or intermediate stage: any silent video + audio pair works."""
    fake = FakeSyncServer(samples["sample.mp4"])
    out = generate_localized_output(
        SAMPLE_VIDEO, SAMPLE_AUDIO, tmp_path / "out", settings=make_settings(), client_factory=fake.client_factory
    )
    assert out.localized_video == tmp_path / "out" / "gen-1.mp4" and out.localized_video.is_file()
    assert fake.uploads["video"] == SAMPLE_VIDEO.read_bytes()
    assert fake.uploads["audio"] == SAMPLE_AUDIO.read_bytes()


def test_output_stage_rejects_files_over_sync_limit(tmp_path):
    big = tmp_path / "big.mp4"
    with big.open("wb") as fh:
        fh.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(MediaFileError) as info:
        load_media_file(big, "video")
    assert info.value.http_status == 413
    assert "under 20MB" in info.value.message


def test_output_stage_rejects_unsupported_audio(tmp_path):
    bad = tmp_path / "speech.txt"
    bad.write_text("x")
    with pytest.raises(MediaFileError, match="Unsupported audio format"):
        load_media_file(bad, "audio")


def test_engine_selection(make_settings):
    assert isinstance(get_engine(make_settings(engine="ishnit")), IshnitEngine)
    assert isinstance(get_engine(make_settings(engine="mock")), MockLocalizationEngine)


def test_engine_contract_requires_all_three_steps():
    class Incomplete(LocalizationEngine):
        def transcribe(self, request): ...

    with pytest.raises(TypeError):
        Incomplete()


def test_language_registry():
    assert [lang.code for lang in LANGUAGES] == ["hi", "en", "te", "ml", "kn", "ta", "pa", "bn"]
    assert get_language("ML").name == "Malayalam"
    assert get_language("hi").label == "Hindi (हिन्दी)"
    with pytest.raises(ValueError, match="Unsupported language"):
        get_language("fr")
