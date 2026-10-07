"""Drive the real Streamlit app headlessly with streamlit.testing (no browser)."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from app.config import get_settings
from app.config.settings import PROJECT_ROOT
from tests.helpers import requires_ffmpeg

APP = str(PROJECT_ROOT / "streamlit_app.py")


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("LOCALIZATION_ENGINE", "mock")
    monkeypatch.setenv("LIPSYNC_PROVIDER", "mock")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _button(at: AppTest):
    return next(b for b in at.button if b.label == "Start localization")


def test_initial_state(app_env):
    at = _app()
    assert at.title[0].value == "Advertisement Video Localization"
    assert "STT/TTT/TTS is mocked" in at.warning[0].value
    uploader = at.get("file_uploader")[0]
    assert {".mp4", ".mov", ".avi", ".mkv"} <= set(uploader.allowed_type)  # Streamlit adds aliases like .mpeg4
    source, target = at.selectbox
    assert source.options[0] == "Auto-detect" and len(source.options) == 9
    assert target.options == [
        "Hindi (हिन्दी)", "English", "Telugu (తెలుగు)", "Malayalam (മലയാളം)",
        "Kannada (ಕನ್ನಡ)", "Tamil (தமிழ்)", "Punjabi (ਪੰਜਾਬੀ)", "Bengali (বাংলা)",
    ]  # fmt: skip
    assert _button(at).disabled  # nothing uploaded yet


def test_same_source_and_target_blocks_start(app_env, samples):
    at = _app()
    at.get("file_uploader")[0].set_value(("ad.mp4", samples["sample.mp4"].read_bytes(), "video/mp4"))
    at.selectbox[0].select_index(1)  # Hindi
    at.selectbox[1].select_index(0)  # Hindi
    at.run()
    assert any("must be different" in e.value for e in at.error)
    assert _button(at).disabled


@requires_ffmpeg
def test_upload_select_language_and_localize(app_env, samples):
    at = _app()
    at.get("file_uploader")[0].set_value(("Summer Sale.mp4", samples["sample.mp4"].read_bytes(), "video/mp4"))
    at.selectbox[1].select_index(5)  # Tamil
    at.run()
    assert not _button(at).disabled

    _button(at).click().run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert at.get("status")[0].proto.label == "Localization complete"
    run_caption = next(c.value for c in at.caption if c.value.startswith("Run ID"))
    assert "Summer_Sale__" in run_caption
    labels = [b.proto.label for b in at.get("download_button")]
    assert "Download localized video" in labels and "Download localized audio" in labels
    assert at.get("video") and at.get("audio")
    assert list((app_env / "runs" / "Summer_Sale").glob("*/localized/mock-*.mp4"))


@requires_ffmpeg
def test_stage_failure_is_reported(app_env, samples, monkeypatch):
    monkeypatch.setenv("LOCALIZATION_ENGINE", "ishnit")
    get_settings.cache_clear()
    at = _app()
    at.get("file_uploader")[0].set_value(("ad.mp4", samples["sample.mp4"].read_bytes(), "video/mp4"))
    at.run()
    _button(at).click().run()
    assert not at.exception, at.exception
    assert at.error[0].value.startswith("STT failed: Ishnit's STT/TTT/TTS implementation is not integrated yet")
    assert at.get("status")[0].proto.label.startswith("STT failed")
