import logging
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.pipeline.output.media_files import MAX_FILE_BYTES, MediaFileError, build_media_file, check_extension
from app.pipeline.output.sync_service import MediaFile, SyncError, SyncService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["output"])


def get_sync_service() -> Iterator[SyncService]:
    settings = get_settings().sync
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="SYNC_API_KEY is not configured on the server")
    service = SyncService(
        api_key=settings.api_key,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        poll_interval_seconds=settings.poll_interval_seconds,
        output_dir=str(settings.output_dir),
    )
    try:
        yield service
    finally:
        service.close()


def _read_upload(upload: UploadFile, field: str) -> MediaFile:
    extension = Path(upload.filename or "").suffix
    try:
        check_extension(extension, field)  # before reading, so unsupported files are rejected cheaply
        return build_media_file(upload.file.read(MAX_FILE_BYTES + 1), extension, field)
    except MediaFileError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post("/sync")
def create_sync(
    video: UploadFile = File(..., description="Input video (.mp4, .mov, .webm, .avi)"),
    audio: UploadFile = File(..., description="Input audio (.wav, .mp3, .ogg, .flac, .m4a, ...)"),
    service: SyncService = Depends(get_sync_service),
):
    logger.info("Received sync request")
    video_file = _read_upload(video, "video")
    audio_file = _read_upload(audio, "audio")
    logger.info("Video size: %d bytes, audio size: %d bytes", len(video_file.content), len(audio_file.content))

    try:
        result = service.run(video_file, audio_file)
    except SyncError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"status": "failed", "generation_id": exc.generation_id, "error": exc.message},
        )

    return {
        "status": "completed",
        "generation_id": result.generation_id,
        "output_file": result.output_file.as_posix(),
    }
