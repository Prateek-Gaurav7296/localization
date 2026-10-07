"""Output stage: lip-sync the silent video to the localized audio (Sync.so)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.pipeline.models import OutputResult
from app.pipeline.output.media_files import MediaFileError, load_media_file
from app.pipeline.output.sync_service import MediaFile, SyncError, SyncResult, SyncService


class LipSyncClient(Protocol):
    """What the output stage needs from a lip-sync provider (``SyncService`` satisfies it)."""

    def run(self, video: MediaFile, audio: MediaFile) -> SyncResult: ...

    def close(self) -> None: ...


LipSyncClientFactory = Callable[[Path], LipSyncClient]


def create_lipsync_client(settings: Settings, output_dir: Path) -> LipSyncClient:
    """Build the provider selected by ``LIPSYNC_PROVIDER``, writing results into ``output_dir``."""
    if settings.pipeline.lipsync_provider == "mock":
        from app.dev.mock_lipsync import MockLipSyncService  # development only

        return MockLipSyncService(output_dir, settings.input)
    sync = settings.sync
    if not sync.api_key:
        raise SyncError("SYNC_API_KEY is not configured", http_status=500)
    return SyncService(
        api_key=sync.api_key,
        model=sync.model,
        timeout_seconds=sync.timeout_seconds,
        poll_interval_seconds=sync.poll_interval_seconds,
        output_dir=str(output_dir),
    )


def generate_localized_output(
    silent_video: Path,
    localized_audio: Path,
    output_dir: Path,
    *,
    settings: Settings,
    client_factory: LipSyncClientFactory | None = None,
) -> OutputResult:
    """Lip-sync ``silent_video`` to ``localized_audio`` and save the result in ``output_dir``.

    Independent of the UI and of the intermediate stage: any video + audio pair works.
    Raises ``MediaFileError`` (bad/oversized file) or ``SyncError`` (provider failure).
    """
    video = load_media_file(silent_video, "video")
    audio = load_media_file(localized_audio, "audio")
    output_dir.mkdir(parents=True, exist_ok=True)
    client = client_factory(output_dir) if client_factory else create_lipsync_client(settings, output_dir)
    try:
        result = client.run(video, audio)
    finally:
        client.close()
    return OutputResult(
        localized_video=result.output_file,
        provider=settings.pipeline.lipsync_provider,
        generation_id=result.generation_id,
    )


__all__ = [
    "LipSyncClient",
    "LipSyncClientFactory",
    "MediaFileError",
    "SyncError",
    "create_lipsync_client",
    "generate_localized_output",
]
