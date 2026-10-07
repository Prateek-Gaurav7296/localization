"""Validation and loading of the video/audio files sent to Sync.so.

Shared by the pipeline (files on disk) and the POST /sync API (uploaded files).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from app.pipeline.output.sync_service import MediaFile

# Formats from https://sync.so/docs/compatibility-and-tips/media-formats-support
VIDEO_EXTENSIONS = {".mp4", ".mov", ".qt", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".alac", ".mp4", ".wma", ".m4a", ".m3a", ".aac"}
# Direct multipart uploads to POST /v2/generate must be under 20MB per file.
MAX_FILE_BYTES = 20 * 1024 * 1024

_ALLOWED = {"video": VIDEO_EXTENSIONS, "audio": AUDIO_EXTENSIONS}


class MediaFileError(ValueError):
    """The file can't be sent to Sync.so. ``http_status`` is what the API returns for it."""

    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def check_extension(extension: str, field: str) -> str:
    extension = extension.lower()
    allowed = _ALLOWED[field]
    if extension not in allowed:
        raise MediaFileError(
            f"Unsupported {field} format '{extension or 'none'}'. Supported: {', '.join(sorted(allowed))}"
        )
    return extension


def build_media_file(content: bytes, extension: str, field: str) -> MediaFile:
    """Validate already-read content (read at most ``MAX_FILE_BYTES + 1`` bytes) into a MediaFile."""
    extension = check_extension(extension, field)
    if not content:
        raise MediaFileError(f"The {field} file is empty")
    if len(content) > MAX_FILE_BYTES:
        raise MediaFileError(f"The {field} file exceeds the 20MB limit", http_status=413)
    content_type = mimetypes.guess_type(f"x{extension}")[0] or "application/octet-stream"
    return MediaFile(filename=f"{field}{extension}", content=content, content_type=content_type)


def load_media_file(path: Path, field: str) -> MediaFile:
    """Load a file from disk for Sync.so, checking format and size before reading it into memory."""
    check_extension(path.suffix, field)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise MediaFileError(
            f"The {field} file is {size / (1024 * 1024):.1f}MB; Sync.so direct uploads must be under 20MB",
            http_status=413,
        )
    return build_media_file(path.read_bytes(), path.suffix, field)
