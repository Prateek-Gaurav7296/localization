"""End-to-end extraction through the API, verified independently with ffprobe."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.pipeline.input.file_service import TIMESTAMP_FORMAT
from tests.conftest import upload
from tests.helpers import DURATION, format_duration, probe_json, requires_ffmpeg, stream_indexes

pytestmark = requires_ffmpeg

# sample -> (expected input audio tracks, expected video strategy, expected audio strategy)
CASES = {
    "sample.mp4": (1, "copy", "copy"),
    "sample.mov": (1, "copy", "encode"),
    "sample.avi": (1, "reencode", "encode"),
    "sample_mpeg4.avi": (1, "copy", "encode"),
    "sample.mkv": (2, "copy", "mixed"),
    "odd_size.mkv": (1, "reencode", "encode"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("sample", list(CASES))
def test_extract_supported_formats(client, input_root, samples, sample):
    tracks, video_strategy, audio_strategy = CASES[sample]
    source = samples[sample]
    stem, ext = source.stem, source.suffix.lstrip(".")

    response = upload(client, source)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["video_strategy"] == video_strategy
    assert body["audio_strategy"] == audio_strategy
    assert body["audio_track_count"] == tracks

    # Directory layout: input/<name>/<timestamp>/{input,output,metadata.json}
    run_dirs = list((input_root / stem).iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    datetime.strptime(run_dir.name, TIMESTAMP_FORMAT)
    assert body["run_id"] == f"{stem}__{run_dir.name}"
    original = run_dir / "input" / f"original_video.{ext}"
    video = run_dir / "output" / "video.mp4"
    audio = run_dir / "output" / "audio.m4a"
    assert sorted(p.name for p in (run_dir / "output").iterdir()) == ["audio.m4a", "video.mp4"]
    assert body["input_file"] == f"input/{stem}/{run_dir.name}/input/original_video.{ext}"
    assert body["video_file"] == f"input/{stem}/{run_dir.name}/output/video.mp4"
    assert body["audio_file"] == f"input/{stem}/{run_dir.name}/output/audio.m4a"

    # Original preserved byte-for-byte.
    assert _sha256(original) == _sha256(source)

    # video.mp4: MP4 container, video present, NO audio.
    assert stream_indexes(video, "v") != []
    assert stream_indexes(video, "a") == []
    assert "mp4" in probe_json(video)["format"]["format_name"]
    assert format_duration(video) == pytest.approx(DURATION, abs=0.2)

    # audio.m4a: every input audio track present, no video, full duration.
    assert len(stream_indexes(audio, "a")) == tracks
    assert stream_indexes(audio, "v") == []
    assert format_duration(audio) == pytest.approx(DURATION, abs=0.15)
    assert body["audio_duration_seconds"] == pytest.approx(DURATION, abs=0.15)

    # metadata.json
    meta = json.loads((run_dir / "metadata.json").read_text())
    assert meta["processing_status"] == "success"
    assert meta["input_filename"] == source.name
    assert meta["input_format"] == ext
    assert meta["video_output"] == "output/video.mp4"
    assert meta["audio_output"] == "output/audio.m4a"
    assert meta["audio_track_count"] == tracks
    assert len([s for s in meta["input_media"]["streams"] if s["codec_type"] == "audio"]) == tracks
    datetime.fromisoformat(meta["timestamp"])
    assert str(input_root) not in json.dumps(meta)  # no absolute server paths leaked

    # Downloads return exactly the files on disk.
    video_dl = client.get(f"/download/{body['run_id']}/video")
    assert video_dl.status_code == 200
    assert video_dl.headers["content-type"] == "video/mp4"
    assert 'filename="video.mp4"' in video_dl.headers["content-disposition"]
    assert video_dl.content == video.read_bytes()
    audio_dl = client.get(f"/download/{body['run_id']}/audio")
    assert audio_dl.status_code == 200
    assert audio_dl.headers["content-type"] == "audio/mp4"
    assert 'filename="audio.m4a"' in audio_dl.headers["content-disposition"]
    assert audio_dl.content == audio.read_bytes()
    assert body["video_download_url"].endswith(f"/download/{body['run_id']}/video")

    run = client.get(f"/runs/{body['run_id']}")
    assert run.status_code == 200
    assert run.json() == meta


def test_multitrack_audio_preserves_codecs_and_languages(client, input_root, samples):
    body = upload(client, samples["sample.mkv"]).json()
    run_dir = input_root / "sample" / body["run_id"].split("__", 1)[1]
    streams = probe_json(run_dir / "output" / "audio.m4a")["streams"]
    assert [s["codec_name"] for s in streams] == ["aac", "aac"]  # opus -> aac, aac copied
    assert [s["channels"] for s in streams] == [2, 6]  # 5.1 layout kept, not downmixed
    assert [s["tags"].get("language") for s in streams] == ["eng", "fra"]


def test_each_upload_gets_unique_run_directory(client, input_root, samples):
    ids = [upload(client, samples["sample.mp4"]).json()["run_id"] for _ in range(3)]
    assert len(set(ids)) == 3
    run_dirs = sorted(p.name for p in (input_root / "sample").iterdir())
    assert len(run_dirs) == 3
    assert all((input_root / "sample" / d / "output" / "video.mp4").is_file() for d in run_dirs)


def test_path_traversal_filename_is_contained(client, input_root, samples):
    response = upload(client, samples["sample.mp4"], filename="../../../tmp/evil name.mp4")
    assert response.status_code == 200, response.text
    assert [p.name for p in input_root.iterdir()] == ["evil_name"]
    assert response.json()["run_id"].startswith("evil_name__")


def test_ffmpeg_failure_returns_500_and_cleans_up(client, input_root, samples, monkeypatch, tmp_path):
    from app.config import get_settings

    failing = tmp_path / "fake-ffmpeg"
    # Writes a partial output file (last arg is the output path), then fails like a real encoder crash.
    failing.write_text(
        '#!/bin/sh\nfor last; do :; done\necho partial > "$last"\n'
        "echo 'simulated encoder explosion /secret/path' >&2\nexit 1\n"
    )
    failing.chmod(0o755)
    monkeypatch.setenv("FFMPEG_BINARY", str(failing))
    get_settings.cache_clear()

    response = upload(client, samples["sample.mp4"])
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == "Video extraction failed. Check server logs for details."
    assert "secret" not in response.text and "Traceback" not in response.text

    run_dir = next((input_root / "sample").iterdir())
    assert list((run_dir / "output").iterdir()) == []  # partial outputs removed
    meta = json.loads((run_dir / "metadata.json").read_text())
    assert meta["processing_status"] == "failed"
    assert meta["error"]["http_status"] == 500
    assert "simulated encoder explosion" in meta["error"]["detail"]  # kept for operators


def test_missing_ffmpeg_binary_returns_500(client, input_root, samples, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FFMPEG_BINARY", "definitely-not-ffmpeg-xyz")
    get_settings.cache_clear()
    response = upload(client, samples["sample.mp4"])
    assert response.status_code == 500
    assert "Check server logs" in response.json()["detail"]
