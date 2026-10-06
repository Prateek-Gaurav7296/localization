"""Test helpers: synthetic media generation and independent ffprobe checks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

requires_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")

DURATION = 3  # seconds of synthetic media


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", *args], check=True)


def _video_src(size: str = "320x240") -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={DURATION}"]


def _tone(freq: int, channel_layout: str = "stereo") -> list[str]:
    return [
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:sample_rate=48000:duration={DURATION},aformat=channel_layouts={channel_layout}",
    ]  # fmt: skip


def generate_samples(directory: Path) -> dict[str, Path]:
    """Create one small clip per case. Codecs are chosen to exercise copy, mixed and re-encode paths."""
    directory.mkdir(parents=True, exist_ok=True)
    p = {name: directory / name for name in (
        "sample.mp4", "sample.mov", "sample.avi", "sample_mpeg4.avi", "sample.mkv",
        "no_audio.mp4", "audio_only.mp4", "corrupt.mp4", "truncated.mp4", "odd_size.mkv",
    )}  # fmt: skip

    # MP4: H.264 + AAC -> video copy, audio copy.
    ffmpeg(*_video_src(), *_tone(440), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(p["sample.mp4"]))
    # MOV: H.264 + PCM -> video copy, audio encoded to AAC.
    ffmpeg(*_video_src(), *_tone(550), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
           str(p["sample.mov"]))  # fmt: skip
    # AVI: MJPEG + MP3 -> video re-encoded to H.264, audio encoded.
    ffmpeg(*_video_src(), *_tone(660), "-c:v", "mjpeg", "-q:v", "5", "-c:a", "libmp3lame", str(p["sample.avi"]))
    # AVI: MPEG-4 Part 2 + PCM -> video copy path from AVI.
    ffmpeg(*_video_src(), *_tone(330), "-c:v", "mpeg4", "-q:v", "5", "-c:a", "pcm_s16le", str(p["sample_mpeg4.avi"]))
    # MKV: VP9 + two audio tracks (Opus "dialogue" + AAC 5.1 "music") -> mixed copy/encode, multi-track.
    ffmpeg(
        *_video_src(), *_tone(440), *_tone(880, "5.1"),
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libvpx-vp9", "-b:v", "200k", "-deadline", "realtime", "-cpu-used", "8",
        "-c:a:0", "libopus", "-c:a:1", "aac",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fra",
        str(p["sample.mkv"]),
    )  # fmt: skip
    # MKV with odd dimensions and MPEG-2 video -> re-encode must fix yuv420p even-size constraint.
    ffmpeg(*_video_src("321x241"), *_tone(500), "-c:v", "mpeg2video", "-c:a", "libmp3lame", str(p["odd_size.mkv"]))
    # Video without audio.
    ffmpeg(*_video_src(), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p["no_audio.mp4"]))
    # Audio only, but with a video extension.
    ffmpeg(*_tone(440), "-c:a", "aac", "-f", "mp4", str(p["audio_only.mp4"]))
    # Random bytes with a valid extension.
    p["corrupt.mp4"].write_bytes(b"\x00\x01not-a-real-video\xff" * 4096)
    # A real MP4 cut short (moov atom at the end is lost).
    data = p["sample.mp4"].read_bytes()
    p["truncated.mp4"].write_bytes(data[: len(data) // 2])
    return p


def probe_json(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(out.stdout)


def stream_indexes(path: Path, selector: str) -> list[str]:
    """Exactly the check from the spec: ``ffprobe -select_streams <a|v> -show_entries stream=index``."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", selector, "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [line for line in out.stdout.splitlines() if line.strip()]


def format_duration(path: Path) -> float:
    return float(probe_json(path)["format"]["duration"])
