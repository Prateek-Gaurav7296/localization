from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config import InputSettings, PipelineSettings, Settings, SyncSettings
from app.pipeline.output.sync_service import SyncService


class FakeSyncServer:
    """Stands in for api.sync.so; the 'generated' video it returns is a real MP4 we supply."""

    def __init__(self, result_video: Path, final_status: str = "COMPLETED", error: str | None = None):
        self.result_bytes = result_video.read_bytes()
        self.final_status = final_status
        self.error = error
        self.uploads: dict[str, bytes] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v2/generate":
            self.uploads = _multipart_files(request)
            return httpx.Response(201, json={"id": "gen-1", "status": "PENDING"})
        if request.method == "GET" and request.url.path == "/v2/generate/gen-1":
            body = {"id": "gen-1", "status": self.final_status}
            if self.final_status == "COMPLETED":
                body["outputUrl"] = "https://cdn.example.com/out/gen-1.mp4?sig=x"
            else:
                body["error"] = self.error
            return httpx.Response(200, json=body)
        if request.url.host == "cdn.example.com":
            return httpx.Response(200, content=self.result_bytes)
        return httpx.Response(404)

    def client_factory(self, output_dir: Path) -> SyncService:
        return SyncService(
            api_key="test-key",
            model="sync-3",
            timeout_seconds=5,
            poll_interval_seconds=0,
            output_dir=str(output_dir),
            transport=httpx.MockTransport(self.handler),
        )


def _multipart_files(request: httpx.Request) -> dict[str, bytes]:
    """Extract {field: file bytes} from a multipart request body."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    files = {}
    for part in request.content.split(b"--" + boundary):
        head, _, body = part.partition(b"\r\n\r\n")
        if b'filename="' in head:
            name = head.split(b'name="')[1].split(b'"')[0].decode()
            files[name] = body.removesuffix(b"\r\n")
    return files


@pytest.fixture
def make_settings(tmp_path: Path):
    def _make(engine: str = "mock", lipsync: str = "sync", sync_api_key: str = "test-key") -> Settings:
        return Settings(
            data_dir=tmp_path,
            log_level="INFO",
            input=InputSettings(
                runs_root=tmp_path / "runs",
                ffmpeg_binary="ffmpeg",
                ffprobe_binary="ffprobe",
                max_upload_size_mb=100,
                ffmpeg_timeout_seconds=120,
            ),
            sync=SyncSettings(
                api_key=sync_api_key,
                model="sync-3",
                timeout_seconds=5,
                poll_interval_seconds=0,
                output_dir=tmp_path / "sync_output",
            ),
            pipeline=PipelineSettings(engine=engine, lipsync_provider=lipsync),
        )

    return _make
