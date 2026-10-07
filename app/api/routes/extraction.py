"""Extraction, download and run-lookup endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_input_settings
from app.api.schemas import ErrorResponse, ExtractionResponse, OutputKind
from app.config import InputSettings
from app.pipeline.input import extraction_service, file_service
from app.pipeline.input.errors import ExtractionError, RunNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["extraction"])

SettingsDep = Annotated[InputSettings, Depends(get_input_settings)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Unsupported format, invalid/corrupt media, or no audio stream"},
    413: {"model": ErrorResponse, "description": "Upload exceeds MAX_UPLOAD_SIZE_MB"},
    500: {"model": ErrorResponse, "description": "FFmpeg processing failed"},
}

_OUTPUTS = {
    OutputKind.video: (file_service.VIDEO_OUTPUT_NAME, "video/mp4"),
    OutputKind.audio: (file_service.AUDIO_OUTPUT_NAME, "audio/mp4"),
}


# Sync handler on purpose: FastAPI runs it in a worker thread, so the blocking file copy and
# ffmpeg subprocesses never stall the event loop.
@router.post("/extract", response_model=ExtractionResponse, responses=_ERROR_RESPONSES)
def extract(
    request: Request,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Video file (.mp4, .mov, .avi, .mkv)")],
) -> ExtractionResponse:
    """Split an uploaded video into a silent ``video.mp4`` and an ``audio.m4a`` with all audio tracks."""
    try:
        outcome = extraction_service.process_upload(file.file, file.filename or "", settings)
    except ExtractionError as exc:
        raise _to_http(exc) from exc
    finally:
        file.file.close()

    run = outcome.run
    meta = outcome.metadata
    base = settings.runs_root.parent

    def url(kind: OutputKind) -> str:
        return str(request.url_for("download_output", run_id=run.run_id, kind=kind.value))

    return ExtractionResponse(
        status="success",
        run_id=run.run_id,
        input_file=outcome.original_input.relative_to(base).as_posix(),
        video_file=run.video_output.relative_to(base).as_posix(),
        audio_file=run.audio_output.relative_to(base).as_posix(),
        metadata_file=run.metadata_file.relative_to(base).as_posix(),
        video_download_url=url(OutputKind.video),
        audio_download_url=url(OutputKind.audio),
        run_url=str(request.url_for("get_run", run_id=run.run_id)),
        video_duration_seconds=meta["video_duration_seconds"],
        audio_duration_seconds=meta["audio_duration_seconds"],
        audio_track_count=meta["audio_track_count"],
        video_strategy=meta["video_strategy"],
        audio_strategy=meta["audio_strategy"],
    )


@router.get(
    "/download/{run_id}/{kind}",
    name="download_output",
    response_class=FileResponse,
    responses={404: {"model": ErrorResponse}},
)
def download_output(run_id: str, kind: OutputKind, settings: SettingsDep) -> FileResponse:
    """Download ``video.mp4`` or ``audio.m4a`` from a completed run."""
    try:
        run = file_service.resolve_run(settings.runs_root, run_id)
    except ExtractionError as exc:
        raise _to_http(exc) from exc
    filename, media_type = _OUTPUTS[kind]
    path = run.output_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No {kind.value} output for run '{run_id}'")
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/runs/{run_id}", name="get_run", responses={404: {"model": ErrorResponse}})
def get_run(run_id: str, settings: SettingsDep) -> dict[str, Any]:
    """Return the run's ``metadata.json`` (including failure details for failed runs)."""
    try:
        run = file_service.resolve_run(settings.runs_root, run_id)
        return file_service.read_metadata(run.metadata_file)
    except RunNotFoundError as exc:
        raise _to_http(exc) from exc


def _to_http(exc: ExtractionError) -> HTTPException:
    if exc.status_code >= 500:
        # Internal detail (ffmpeg stderr etc.) is logged and stored in metadata, never returned.
        return HTTPException(status_code=exc.status_code, detail=f"{exc.message}. Check server logs for details.")
    return HTTPException(status_code=exc.status_code, detail=exc.message)
