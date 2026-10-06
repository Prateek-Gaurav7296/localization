import logging
import mimetypes
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.sync_service import MediaFile, SyncError, SyncService

logger = logging.getLogger(__name__)

router = APIRouter()

# Formats from https://sync.so/docs/compatibility-and-tips/media-formats-support
VIDEO_EXTENSIONS = {".mp4", ".mov", ".qt", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".alac", ".mp4", ".wma", ".m4a", ".m3a", ".aac"}
# Direct multipart uploads to POST /v2/generate must be under 20MB per file.
MAX_FILE_BYTES = 20 * 1024 * 1024


def get_sync_service() -> Iterator[SyncService]:
    settings = get_settings()
    if not settings.sync_api_key:
        raise HTTPException(status_code=500, detail="SYNC_API_KEY is not configured on the server")
    service = SyncService(
        api_key=settings.sync_api_key,
        model=settings.sync_model,
        timeout_seconds=settings.sync_timeout_seconds,
        poll_interval_seconds=settings.sync_poll_interval_seconds,
        output_dir=settings.sync_output_dir,
    )
    try:
        yield service
    finally:
        service.close()


def _read_upload(upload: UploadFile, field: str, allowed_extensions: set[str]) -> MediaFile:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported {field} format '{extension or 'none'}'. "
            f"Supported: {', '.join(sorted(allowed_extensions))}",
        )
    content = upload.file.read(MAX_FILE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail=f"The {field} file is empty")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"The {field} file exceeds the 20MB limit")
    content_type = mimetypes.guess_type(f"x{extension}")[0] or "application/octet-stream"
    return MediaFile(filename=f"{field}{extension}", content=content, content_type=content_type)


@router.post("/sync")
def create_sync(
    video: UploadFile = File(..., description="Input video (.mp4, .mov, .webm, .avi)"),
    audio: UploadFile = File(..., description="Input audio (.wav, .mp3, .ogg, .flac, .m4a, ...)"),
    service: SyncService = Depends(get_sync_service),
):
    logger.info("Received sync request")
    video_file = _read_upload(video, "video", VIDEO_EXTENSIONS)
    audio_file = _read_upload(audio, "audio", AUDIO_EXTENSIONS)
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
