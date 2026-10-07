"""FFmpeg invocations for splitting a video into a silent MP4 and an M4A audio file.

Strategy: stream-copy whenever the codec is safe in the target container (fast, lossless),
verify the result with ffprobe, and fall back to re-encoding if the copy fails or the output
does not verify.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.pipeline.input.errors import ExtractionError, ProcessingError
from app.pipeline.input.media_service import MediaInfo, StreamInfo, probe

# Video codecs that can be stream-copied into an MP4 container and play broadly.
MP4_COPYABLE_VIDEO_CODECS = frozenset({"h264", "hevc", "av1", "mpeg4", "vp9"})
# Audio codecs that can be stream-copied into an .m4a (MP4 audio) container.
M4A_COPYABLE_AUDIO_CODECS = frozenset({"aac", "alac"})

AAC_STEREO_BITRATE_KBPS = 192
AAC_PER_CHANNEL_BITRATE_KBPS = 64
STDERR_TAIL_CHARS = 4000


@dataclass(frozen=True)
class ExtractionResult:
    path: Path
    strategy: str
    info: MediaInfo
    command: list[str]


class FFmpegError(ProcessingError):
    pass


def run_ffmpeg(ffmpeg_binary: str, args: list[str], timeout: int, log: logging.LoggerAdapter) -> list[str]:
    """Run ffmpeg with an argument list (never a shell). Returns the command on success."""
    cmd = [ffmpeg_binary, "-hide_banner", "-nostdin", "-y", "-v", "error", *args]
    log.debug("ffmpeg command: %s", cmd)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise FFmpegError("Media processing tool is unavailable", detail=f"ffmpeg not found: {ffmpeg_binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("Media processing timed out", detail=f"ffmpeg exceeded {timeout}s") from exc

    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        raise FFmpegError(
            "Media processing failed",
            detail=f"ffmpeg exit {result.returncode}: {stderr[-STDERR_TAIL_CHARS:]}",
        )
    if stderr:
        log.debug("ffmpeg stderr: %s", stderr[-STDERR_TAIL_CHARS:])
    return cmd


def extract_video(
    source: MediaInfo,
    output: Path,
    *,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    timeout: int,
    log: logging.LoggerAdapter,
) -> ExtractionResult:
    """Write the primary video stream (no audio, subtitles or data) to ``output`` as MP4."""
    stream = source.primary_video
    strategies = ["copy", "reencode"] if stream.codec_name in MP4_COPYABLE_VIDEO_CODECS else ["reencode"]

    def build(strategy: str) -> list[str]:
        args = ["-fflags", "+genpts", "-i", str(source.path), "-map", f"0:{stream.index}", "-map_metadata", "0"]
        if strategy == "copy":
            args += ["-c:v", "copy"]
            if stream.codec_name == "hevc":
                args += ["-tag:v", "hvc1"]  # Apple players require the hvc1 tag for HEVC in MP4.
        else:
            args += [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                # yuv420p needs even dimensions; this is a no-op for already-even sizes.
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            ]  # fmt: skip
        return [*args, "-an", "-sn", "-dn", "-movflags", "+faststart", "-f", "mp4", str(output)]

    def verify(info: MediaInfo) -> None:
        if not info.video_streams:
            raise ProcessingError("Video output verification failed", detail="output has no video stream")
        if info.audio_streams:
            raise ProcessingError("Video output verification failed", detail="output still contains audio")
        _check_duration("video", expected=source.video_duration(), actual=info.video_duration(), log=log)

    return _run_with_fallback(
        "video", strategies, build, verify, output,
        ffmpeg_binary=ffmpeg_binary, ffprobe_binary=ffprobe_binary, timeout=timeout, log=log,
    )  # fmt: skip


def extract_audio(
    source: MediaInfo,
    output: Path,
    *,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    timeout: int,
    log: logging.LoggerAdapter,
) -> ExtractionResult:
    """Write every audio stream of the input to ``output`` (.m4a), one track per input stream.

    Streams are kept as separate tracks rather than mixed down, so nothing is lost (e.g. a
    dialogue track and a music track, or several languages, all survive unchanged).
    """
    streams = source.audio_streams
    copyable = [s.codec_name in M4A_COPYABLE_AUDIO_CODECS for s in streams]
    if all(copyable):
        strategies = ["copy", "encode"]
    elif any(copyable):
        strategies = ["mixed", "encode"]  # copy the AAC/ALAC tracks, encode the rest
    else:
        strategies = ["encode"]

    def build(strategy: str) -> list[str]:
        args = ["-i", str(source.path)]
        for s in streams:
            args += ["-map", f"0:{s.index}"]
        args += ["-map_metadata", "0"]
        for i, s in enumerate(streams):
            copy = strategy == "copy" or (strategy == "mixed" and s.codec_name in M4A_COPYABLE_AUDIO_CODECS)
            if copy:
                args += [f"-c:a:{i}", "copy"]
            else:
                args += [f"-c:a:{i}", "aac", f"-b:a:{i}", f"{_aac_bitrate_kbps(s)}k"]
        return [*args, "-vn", "-sn", "-dn", "-movflags", "+faststart", "-f", "ipod", str(output)]

    def verify(info: MediaInfo) -> None:
        if info.video_streams:
            raise ProcessingError("Audio output verification failed", detail="output contains video")
        if len(info.audio_streams) != len(streams):
            raise ProcessingError(
                "Audio output verification failed",
                detail=f"expected {len(streams)} audio stream(s), found {len(info.audio_streams)}",
            )
        expected = source.audio_duration()
        actual = info.audio_duration() or info.duration
        _check_duration("audio", expected=expected, actual=actual, log=log, strict=True)

    return _run_with_fallback(
        "audio", strategies, build, verify, output,
        ffmpeg_binary=ffmpeg_binary, ffprobe_binary=ffprobe_binary, timeout=timeout, log=log,
    )  # fmt: skip


def _run_with_fallback(
    kind: str,
    strategies: list[str],
    build: Callable[[str], list[str]],
    verify: Callable[[MediaInfo], None],
    output: Path,
    *,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    timeout: int,
    log: logging.LoggerAdapter,
) -> ExtractionResult:
    last_error: ExtractionError | None = None
    for strategy in strategies:
        log.info("%s extraction attempt: strategy=%s", kind, strategy)
        try:
            cmd = run_ffmpeg(ffmpeg_binary, build(strategy), timeout, log)
            if not output.is_file() or output.stat().st_size == 0:
                raise ProcessingError(f"{kind.capitalize()} output was not created", detail=str(output))
            info = probe(output, ffprobe_binary)
            verify(info)
            return ExtractionResult(path=output, strategy=strategy, info=info, command=cmd)
        except ExtractionError as exc:
            last_error = exc
            output.unlink(missing_ok=True)
            log.warning("%s extraction with strategy=%s failed: %s (%s)", kind, strategy, exc.message, exc.detail)
    assert last_error is not None
    raise ProcessingError(f"{kind.capitalize()} extraction failed", detail=last_error.detail or last_error.message)


def _check_duration(
    kind: str,
    *,
    expected: float | None,
    actual: float | None,
    log: logging.LoggerAdapter,
    strict: bool = False,
) -> None:
    """Compare durations with a tolerance of max(0.5s, 2%). Strict mode raises on mismatch."""
    if not actual:
        raise ProcessingError(f"{kind.capitalize()} output verification failed", detail="output has no duration")
    if not expected:
        log.info("%s duration check skipped: input does not report a stream duration", kind)
        return
    tolerance = max(0.5, expected * 0.02)
    if abs(expected - actual) > tolerance:
        message = f"{kind} duration mismatch: input={expected:.3f}s output={actual:.3f}s"
        if strict:
            raise ProcessingError(f"{kind.capitalize()} output verification failed", detail=message)
        log.warning(message)


def _aac_bitrate_kbps(stream: StreamInfo) -> int:
    channels = stream.channels or 2
    return max(AAC_STEREO_BITRATE_KBPS, AAC_PER_CHANNEL_BITRATE_KBPS * channels)
