"""Mock lip-sync provider (development only).

Same interface as ``SyncService`` but simply replaces the video's audio track with the given
audio using FFmpeg. There is NO lip-sync.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from app.config import InputSettings
from app.pipeline.input.ffmpeg_service import run_ffmpeg
from app.pipeline.output.sync_service import MediaFile, SyncResult

logger = logging.getLogger(__name__)


class MockLipSyncService:
    def __init__(self, output_dir: Path, settings: InputSettings) -> None:
        self.output_dir = Path(output_dir)
        self._settings = settings

    def run(self, video: MediaFile, audio: MediaFile) -> SyncResult:
        generation_id = f"mock-{uuid.uuid4().hex[:12]}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output = self.output_dir / f"{generation_id}.mp4"
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / video.filename
            audio_path = Path(tmp) / audio.filename
            video_path.write_bytes(video.content)
            audio_path.write_bytes(audio.content)
            run_ffmpeg(
                self._settings.ffmpeg_binary,
                [
                    "-i", str(video_path), "-i", str(audio_path),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                    "-movflags", "+faststart", str(output),
                ],
                self._settings.ffmpeg_timeout_seconds,
                logging.LoggerAdapter(logger, {}),
            )  # fmt: skip
        return SyncResult(generation_id=generation_id, output_file=output)

    def close(self) -> None:
        pass
