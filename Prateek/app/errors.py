"""Domain errors. Each carries the HTTP status and a client-safe message."""

from __future__ import annotations


class ExtractionError(Exception):
    """Base class. ``message`` is safe to return to API clients; ``detail`` is for logs only."""

    status_code: int = 500
    stage: str = "processing"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class UnsupportedFormatError(ExtractionError):
    status_code = 400
    stage = "validation"


class InvalidUploadError(ExtractionError):
    status_code = 400
    stage = "validation"


class FileTooLargeError(ExtractionError):
    status_code = 413
    stage = "upload"


class InvalidMediaError(ExtractionError):
    """The upload is not a readable media file, or has no usable video stream."""

    status_code = 400
    stage = "probe"


class NoAudioStreamError(ExtractionError):
    status_code = 400
    stage = "probe"


class ProcessingError(ExtractionError):
    """FFmpeg/FFprobe failed, or produced output that failed verification."""

    status_code = 500
    stage = "processing"


class RunNotFoundError(ExtractionError):
    status_code = 404
    stage = "lookup"
