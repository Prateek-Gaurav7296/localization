"""Runtime configuration, read from environment variables (optionally via a .env file).

Settings are grouped by concern so each pipeline stage only receives what it needs:

* ``InputSettings``    - run directories, FFmpeg/FFprobe, upload limits (input stage)
* ``SyncSettings``     - Sync.so lip-sync API (output stage)
* ``PipelineSettings`` - which intermediate engine / lip-sync provider to use

Languages are configured separately in ``app/config/languages.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SUPPORTED_EXTENSIONS: tuple[str, ...] = ("mp4", "mov", "avi", "mkv")

ENGINE_CHOICES = ("ishnit", "mock")
LIPSYNC_CHOICES = ("sync", "mock")


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


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from exc


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    value = (os.getenv(name) or default).strip().lower()
    if value not in choices:
        raise ValueError(f"Environment variable {name} must be one of {', '.join(choices)}; got {value!r}")
    return value


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class InputSettings:
    runs_root: Path
    ffmpeg_binary: str
    ffprobe_binary: str
    max_upload_size_mb: int
    ffmpeg_timeout_seconds: int

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@dataclass(frozen=True)
class SyncSettings:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float
    poll_interval_seconds: float
    output_dir: Path  # only used by the standalone POST /sync API


@dataclass(frozen=True)
class PipelineSettings:
    engine: str  # "ishnit" (real STT/TTT/TTS) or "mock" (development only)
    lipsync_provider: str  # "sync" (Sync.so) or "mock" (development only)

    @property
    def uses_mocks(self) -> bool:
        return self.engine == "mock" or self.lipsync_provider == "mock"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    log_level: str
    input: InputSettings
    sync: SyncSettings
    pipeline: PipelineSettings


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings. Call ``get_settings.cache_clear()`` after changing env vars."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    data_dir = _env_path("DATA_DIR", Path("data"))
    return Settings(
        data_dir=data_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        input=InputSettings(
            runs_root=_env_path("RUNS_DIR", data_dir / "runs"),
            ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
            ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
            max_upload_size_mb=_env_int("MAX_UPLOAD_SIZE_MB", 2048),
            ffmpeg_timeout_seconds=_env_int("FFMPEG_TIMEOUT_SECONDS", 3600),
        ),
        sync=SyncSettings(
            api_key=os.getenv("SYNC_API_KEY", ""),
            model=os.getenv("SYNC_MODEL", "sync-3"),
            timeout_seconds=_env_float("SYNC_TIMEOUT_SECONDS", 600),
            poll_interval_seconds=_env_float("SYNC_POLL_INTERVAL_SECONDS", 5),
            output_dir=_env_path("SYNC_OUTPUT_DIR", data_dir / "sync_output"),
        ),
        pipeline=PipelineSettings(
            # Defaults to the mock engine until Ishnit's STT/TTT/TTS is integrated; see README.
            engine=_env_choice("LOCALIZATION_ENGINE", "mock", ENGINE_CHOICES),
            lipsync_provider=_env_choice("LIPSYNC_PROVIDER", "sync", LIPSYNC_CHOICES),
        ),
    )
