import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    sync_api_key: str
    sync_model: str
    sync_timeout_seconds: float
    sync_poll_interval_seconds: float
    sync_output_dir: str


def get_settings() -> Settings:
    return Settings(
        sync_api_key=os.getenv("SYNC_API_KEY", ""),
        sync_model=os.getenv("SYNC_MODEL", "sync-3"),
        sync_timeout_seconds=float(os.getenv("SYNC_TIMEOUT_SECONDS", "600")),
        sync_poll_interval_seconds=float(os.getenv("SYNC_POLL_INTERVAL_SECONDS", "5")),
        sync_output_dir=os.getenv("SYNC_OUTPUT_DIR", "output"),
    )
