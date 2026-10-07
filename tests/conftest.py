from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.helpers import FFMPEG, FFPROBE, generate_samples


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not (FFMPEG and FFPROBE):
        pytest.skip("ffmpeg/ffprobe not installed")
    return generate_samples(tmp_path_factory.mktemp("samples"))


@pytest.fixture
def input_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point RUNS_DIR at a temp dir so tests never touch the real ./data/runs."""
    root = tmp_path / "input"
    monkeypatch.setenv("RUNS_DIR", str(root))
    monkeypatch.setenv("FFMPEG_BINARY", "ffmpeg")
    monkeypatch.setenv("FFPROBE_BINARY", "ffprobe")
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def client(input_root: Path) -> TestClient:
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, path: Path, filename: str | None = None, content_type: str = "video/mp4"):
    with path.open("rb") as fh:
        return client.post("/extract", files={"file": (filename or path.name, fh, content_type)})
