"""Runtime configuration, read from environment variables (optionally via a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_EXTENSIONS: tuple[str, ...] = ("mp4", "mov", "avi", "mkv")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"Environment variable {name} must be positive, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    input_root: Path
    ffmpeg_binary: str
    ffprobe_binary: str
    max_upload_size_mb: int
    ffmpeg_timeout_seconds: int
    log_level: str

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings. Call ``get_settings.cache_clear()`` after changing env vars."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    input_root = Path(os.getenv("INPUT_ROOT", "./input")).expanduser()
    if not input_root.is_absolute():
        input_root = PROJECT_ROOT / input_root

    return Settings(
        input_root=input_root.resolve(),
        ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
        ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
        max_upload_size_mb=_env_int("MAX_UPLOAD_SIZE_MB", 2048),
        ffmpeg_timeout_seconds=_env_int("FFMPEG_TIMEOUT_SECONDS", 3600),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
