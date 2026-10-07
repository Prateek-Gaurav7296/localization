"""Filesystem concerns: filename sanitisation, run directories, uploads and metadata."""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from app.config import SUPPORTED_EXTENSIONS
from app.pipeline.input.errors import FileTooLargeError, InvalidUploadError, RunNotFoundError, UnsupportedFormatError

TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"
RUN_ID_SEPARATOR = "__"
MAX_NAME_LENGTH = 100
COPY_CHUNK_SIZE = 1024 * 1024

ORIGINAL_BASENAME = "original_video"
VIDEO_OUTPUT_NAME = "video.mp4"
AUDIO_OUTPUT_NAME = "audio.m4a"
METADATA_NAME = "metadata.json"

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_SEPARATORS = re.compile(r"_{2,}")
_RUN_ID_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+?)__(?P<timestamp>\d{8}_\d{6}_\d{6})$")


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    name: str
    timestamp: datetime
    run_dir: Path

    @property
    def input_dir(self) -> Path:
        return self.run_dir / "input"

    @property
    def output_dir(self) -> Path:
        return self.run_dir / "output"

    @property
    def video_output(self) -> Path:
        return self.output_dir / VIDEO_OUTPUT_NAME

    @property
    def audio_output(self) -> Path:
        return self.output_dir / AUDIO_OUTPUT_NAME

    @property
    def metadata_file(self) -> Path:
        return self.run_dir / METADATA_NAME

    def original_input(self, extension: str) -> Path:
        return self.input_dir / f"{ORIGINAL_BASENAME}.{extension}"


def client_basename(filename: str) -> str:
    """Strip any directory components a client may have put in the filename (POSIX or Windows)."""
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def get_extension(filename: str) -> str:
    """Return the lower-cased extension without the dot, or '' if there is none."""
    base = client_basename(filename)
    _, dot, ext = base.rpartition(".")
    return ext.lower() if dot and ext else ""


def validate_extension(filename: str | None) -> str:
    if not filename or not client_basename(filename).strip():
        raise InvalidUploadError("No file name provided. Upload a video using the 'file' form field.")
    extension = get_extension(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(f"Unsupported video format. Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}")
    return extension


def sanitize_name(filename: str) -> str:
    """Derive a safe single-segment directory name from an uploaded filename.

    Drops directory components and the extension, folds to ASCII, replaces anything outside
    ``[A-Za-z0-9._-]`` with ``_`` and trims leading/trailing dots so the result can never be
    ``.``, ``..`` or a hidden name. Falls back to ``video``.
    """
    base = client_basename(filename)
    stem, dot, _ = base.rpartition(".")
    stem = stem if dot else base
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    safe = _UNSAFE_CHARS.sub("_", ascii_stem)
    safe = _REPEATED_SEPARATORS.sub("_", safe).strip("._-")
    return safe[:MAX_NAME_LENGTH].rstrip("._-") or "video"


def build_run_id(name: str, timestamp: datetime) -> str:
    return f"{name}{RUN_ID_SEPARATOR}{timestamp.strftime(TIMESTAMP_FORMAT)}"


def create_run(input_root: Path, filename: str) -> RunPaths:
    """Create ``<input_root>/<name>/<timestamp>/{input,output}``, unique per call."""
    name = sanitize_name(filename)
    name_dir = _within_root(input_root, input_root / name)
    name_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        timestamp = datetime.now().astimezone()
        run_dir = name_dir / timestamp.strftime(TIMESTAMP_FORMAT)
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        paths = RunPaths(run_id=build_run_id(name, timestamp), name=name, timestamp=timestamp, run_dir=run_dir)
        paths.input_dir.mkdir()
        paths.output_dir.mkdir()
        return paths
    raise RuntimeError(f"Could not allocate a unique run directory under {name_dir}")


def resolve_run(input_root: Path, run_id: str) -> RunPaths:
    """Map a run_id back to its directory, rejecting anything that could escape input_root."""
    match = _RUN_ID_PATTERN.match(run_id)
    if not match or match["name"] in {".", ".."}:
        raise RunNotFoundError(f"Run '{run_id}' not found")
    ts_text = match["timestamp"]
    try:
        timestamp = datetime.strptime(ts_text, TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise RunNotFoundError(f"Run '{run_id}' not found") from exc

    run_dir = _within_root(input_root, input_root / match["name"] / ts_text, not_found=True)
    if not run_dir.is_dir():
        raise RunNotFoundError(f"Run '{run_id}' not found")
    return RunPaths(run_id=run_id, name=match["name"], timestamp=timestamp, run_dir=run_dir)


def save_upload(source: BinaryIO, destination: Path, max_bytes: int) -> int:
    """Stream ``source`` to ``destination`` in chunks, enforcing ``max_bytes``. Returns bytes written."""
    written = 0
    try:
        with destination.open("wb") as out:
            while chunk := source.read(COPY_CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLargeError(
                        f"Uploaded file exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MB"
                    )
                out.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if written == 0:
        destination.unlink(missing_ok=True)
        raise InvalidUploadError("Uploaded file is empty")
    return written


def clear_directory(directory: Path) -> None:
    """Remove everything inside ``directory`` (used to discard partial outputs)."""
    if not directory.is_dir():
        return
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    """Atomically write metadata JSON (write to temp file, then rename)."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_metadata(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RunNotFoundError("Run metadata not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _within_root(root: Path, candidate: Path, *, not_found: bool = False) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        if not_found:
            raise RunNotFoundError("Run not found")
        raise InvalidUploadError("Invalid file name")
    return resolved
