"""Validation and rejection paths: bad extensions, corrupt media, missing streams, traversal, size."""

from __future__ import annotations

import io
import json
from datetime import datetime

import pytest

from app.pipeline.input import file_service
from app.pipeline.input.errors import InvalidUploadError, RunNotFoundError, UnsupportedFormatError
from tests.conftest import upload
from tests.helpers import requires_ffmpeg

UNSUPPORTED_DETAIL = "Unsupported video format. Supported formats: mp4, mov, avi, mkv"


# --- unit: filename handling -------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("sample.mp4", "sample"),
        ("My Holiday Video (1).MOV", "My_Holiday_Video_1"),
        ("../../etc/passwd.mp4", "passwd"),
        ("..\\..\\windows\\evil.avi", "evil"),
        ("/abs/path/clip.mkv", "clip"),
        ("....mp4", "video"),
        (".hidden.mp4", "hidden"),
        ("..", "video"),
        ("café über.mp4", "cafe_uber"),
        ("视频.mp4", "video"),
        ("a" * 300 + ".mp4", "a" * 100),
        ("archive.tar.mp4", "archive.tar"),
    ],
)
def test_sanitize_name(filename, expected):
    assert file_service.sanitize_name(filename) == expected


@pytest.mark.parametrize("filename", ["a.mp4", "b.MOV", "c.Avi", "d.mkv", "../x.MP4"])
def test_validate_extension_accepts_supported(filename):
    assert file_service.validate_extension(filename) in {"mp4", "mov", "avi", "mkv"}


@pytest.mark.parametrize("filename", ["file.txt", "video.webm", "noext", "mp4", "video.mp4.exe", "x."])
def test_validate_extension_rejects_unsupported(filename):
    with pytest.raises(UnsupportedFormatError):
        file_service.validate_extension(filename)


@pytest.mark.parametrize("filename", ["", None, "  ", "dir/", ".mp4/"])
def test_validate_extension_requires_name(filename):
    with pytest.raises(InvalidUploadError):
        file_service.validate_extension(filename)


def test_create_run_layout_and_uniqueness(tmp_path):
    runs = [file_service.create_run(tmp_path, "../My Clip.mp4") for _ in range(5)]
    assert len({r.run_id for r in runs}) == 5
    for run in runs:
        assert run.run_dir.parent == (tmp_path / "My_Clip").resolve()
        assert run.input_dir.is_dir() and run.output_dir.is_dir()
        datetime.strptime(run.run_dir.name, file_service.TIMESTAMP_FORMAT)
        assert file_service.resolve_run(tmp_path, run.run_id).run_dir == run.run_dir


@pytest.mark.parametrize(
    "run_id",
    [
        "..__20261004_103015_123456",
        ".__20261004_103015_123456",
        "../etc__20261004_103015_123456",
        "sample__20261004_103015",
        "sample",
        "sample__99999999_999999_999999",
    ],
)
def test_resolve_run_rejects_bad_ids(tmp_path, run_id):
    with pytest.raises(RunNotFoundError):
        file_service.resolve_run(tmp_path, run_id)


def test_save_upload_enforces_size_and_emptiness(tmp_path):
    dest = tmp_path / "out.bin"
    assert file_service.save_upload(io.BytesIO(b"x" * 10), dest, max_bytes=10) == 10
    with pytest.raises(Exception, match="maximum allowed size"):
        file_service.save_upload(io.BytesIO(b"x" * 11), dest, max_bytes=10)
    assert not dest.exists()
    with pytest.raises(InvalidUploadError, match="empty"):
        file_service.save_upload(io.BytesIO(b""), dest, max_bytes=10)
    assert not dest.exists()


# --- API: rejection paths ------------------------------------------------------------------


def test_unsupported_extension_returns_400_without_creating_run(client, input_root):
    response = client.post("/extract", files={"file": ("file.txt", b"hello", "text/plain")})
    assert response.status_code == 400
    assert response.json() == {"detail": UNSUPPORTED_DETAIL}
    assert not any(input_root.iterdir())


def test_missing_file_field_returns_422(client):
    response = client.post("/extract", data={"other": "x"})
    assert response.status_code == 422


def test_empty_upload_returns_400(client):
    response = client.post("/extract", files={"file": ("empty.mp4", b"", "video/mp4")})
    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty"


def _single_run(input_root, name):
    runs = list((input_root / name).iterdir())
    assert len(runs) == 1
    return runs[0]


def _assert_failed_run(run_dir, stage):
    meta = json.loads((run_dir / "metadata.json").read_text())
    assert meta["processing_status"] == "failed"
    assert meta["error"]["stage"] == stage
    assert list((run_dir / "output").iterdir()) == []
    return meta


@requires_ffmpeg
@pytest.mark.parametrize("sample", ["corrupt.mp4", "truncated.mp4"])
def test_corrupt_video_rejected(client, input_root, samples, sample):
    response = upload(client, samples[sample])
    assert response.status_code == 400
    assert response.json() == {"detail": "Uploaded file is not a valid or readable video file"}
    run_dir = _single_run(input_root, sample.removesuffix(".mp4"))
    meta = _assert_failed_run(run_dir, "probe")
    assert "ffprobe" in meta["error"]["detail"]
    # The original upload is still kept for debugging.
    assert (run_dir / "input" / "original_video.mp4").is_file()


@requires_ffmpeg
def test_no_audio_video_rejected(client, input_root, samples):
    response = upload(client, samples["no_audio.mp4"])
    assert response.status_code == 400
    assert response.json() == {"detail": "Uploaded video has no audio stream, so there is no audio to extract"}
    meta = _assert_failed_run(_single_run(input_root, "no_audio"), "probe")
    assert meta["error"]["type"] == "NoAudioStreamError"
    assert meta["input_media"]["streams"][0]["codec_type"] == "video"


@requires_ffmpeg
def test_audio_only_file_rejected(client, input_root, samples):
    response = upload(client, samples["audio_only.mp4"])
    assert response.status_code == 400
    assert response.json() == {"detail": "Uploaded file contains no video stream"}


@requires_ffmpeg
def test_mislabelled_text_file_rejected(client, input_root):
    response = client.post("/extract", files={"file": ("notes.mp4", b"just some text\n" * 100, "video/mp4")})
    assert response.status_code == 400
    assert "not a valid" in response.json()["detail"]


def test_oversized_upload_rejected_early_by_content_length(client, input_root, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    response = client.post("/extract", files={"file": ("big.mp4", b"x" * (3 * 1024 * 1024), "video/mp4")})
    assert response.status_code == 413
    assert "maximum allowed size of 1 MB" in response.json()["detail"]
    assert not any(input_root.iterdir())  # rejected before any run directory was created


def test_oversized_upload_rejected_while_streaming(client, input_root, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    # Slightly over 1 MB: passes the Content-Length pre-check (which allows multipart overhead)
    # and is caught by the chunked copy instead.
    response = client.post("/extract", files={"file": ("big.mp4", b"x" * (1024 * 1024 + 10), "video/mp4")})
    assert response.status_code == 413
    run_dir = _single_run(input_root, "big")
    _assert_failed_run(run_dir, "upload")
    assert list((run_dir / "input").iterdir()) == []


def test_download_unknown_run_returns_404(client):
    assert client.get("/download/nope__20261004_103015_123456/video").status_code == 404
    assert client.get("/download/..__20261004_103015_123456/audio").status_code == 404
    assert client.get("/runs/nope__20261004_103015_123456").status_code == 404
    assert client.get("/download/nope__20261004_103015_123456/subtitles").status_code == 422
