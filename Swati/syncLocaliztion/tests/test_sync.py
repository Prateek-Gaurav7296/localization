"""Tests for POST /sync. Sync.so is mocked with httpx.MockTransport; no real API calls."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes.sync import get_sync_service
from app.services.sync_service import SyncService

OUTPUT_URL = "https://cdn.example.com/signed/result.mp4?sig=abc"
VIDEO = ("input.mp4", b"fake-video-bytes", "video/mp4")
AUDIO = ("input.wav", b"fake-audio-bytes", "audio/wav")


class FakeSync:
    """Mock Sync.so API: a create response, a sequence of status responses, and the output file."""

    def __init__(self, statuses=("COMPLETED",), create_status=201, create_body=None,
                 download_status=200, failure_error=None):
        self.statuses = list(statuses)
        self.create_status = create_status
        self.create_body = create_body or {"id": "gen-123", "status": "PENDING"}
        self.download_status = download_status
        self.failure_error = failure_error
        self.create_calls = 0
        self.poll_calls = 0
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path == "/v2/generate":
            self.create_calls += 1
            return httpx.Response(self.create_status, json=self.create_body)
        if request.method == "GET" and request.url.path == "/v2/generate/gen-123":
            status = self.statuses[min(self.poll_calls, len(self.statuses) - 1)]
            self.poll_calls += 1
            body = {"id": "gen-123", "status": status}
            if status == "COMPLETED":
                body["outputUrl"] = OUTPUT_URL
            if status == "FAILED":
                body["error"] = self.failure_error
                body["errorCode"] = "generation_failed"
            return httpx.Response(200, json=body)
        if request.url.host == "cdn.example.com":
            return httpx.Response(self.download_status, content=b"generated-video")
        return httpx.Response(404)


@pytest.fixture
def make_client(tmp_path):
    def _make(fake: FakeSync, timeout_seconds=5.0, poll_interval_seconds=0.0):
        def override():
            service = SyncService(
                api_key="test-key",
                model="sync-3",
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                output_dir=str(tmp_path / "output"),
                transport=httpx.MockTransport(fake.handler),
            )
            yield service
            service.close()

        app.dependency_overrides[get_sync_service] = override
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_missing_video(make_client):
    response = make_client(FakeSync()).post("/sync", files={"audio": AUDIO})
    assert response.status_code == 422
    assert any(err["loc"] == ["body", "video"] for err in response.json()["detail"])


def test_missing_audio(make_client):
    response = make_client(FakeSync()).post("/sync", files={"video": VIDEO})
    assert response.status_code == 422
    assert any(err["loc"] == ["body", "audio"] for err in response.json()["detail"])


def test_unsupported_and_empty_files(make_client):
    fake = FakeSync()
    client = make_client(fake)
    assert client.post("/sync", files={"video": ("a.txt", b"x", "text/plain"), "audio": AUDIO}).status_code == 400
    assert client.post("/sync", files={"video": ("a.mp4", b"", "video/mp4"), "audio": AUDIO}).status_code == 400
    assert fake.create_calls == 0


def test_successful_generation_polls_and_downloads(make_client, tmp_path):
    fake = FakeSync(statuses=["PENDING", "PROCESSING", "PROCESSING", "COMPLETED"])
    response = make_client(fake).post("/sync", files={"video": VIDEO, "audio": AUDIO})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["generation_id"] == "gen-123"
    assert body["output_file"].endswith("output/gen-123.mp4")
    assert (tmp_path / "output" / "gen-123.mp4").read_bytes() == b"generated-video"
    assert fake.create_calls == 1
    assert fake.poll_calls == 4

    create = fake.requests[0]
    assert create.headers["x-api-key"] == "test-key"
    assert create.headers["content-type"].startswith("multipart/form-data")
    assert b'name="model"\r\n\r\nsync-3' in create.content
    assert b'name="video"; filename="video.mp4"' in create.content
    assert b'name="audio"; filename="audio.wav"' in create.content

    download = fake.requests[-1]
    assert download.url.host == "cdn.example.com"
    assert "x-api-key" not in download.headers


def test_generation_failure(make_client):
    fake = FakeSync(statuses=["PROCESSING", "FAILED"], failure_error="No face detected")
    response = make_client(fake).post("/sync", files={"video": VIDEO, "audio": AUDIO})

    assert response.status_code == 502
    assert response.json() == {
        "status": "failed",
        "generation_id": "gen-123",
        "error": "Sync generation failed: No face detected",
    }
    assert fake.create_calls == 1  # no retry


def test_timeout(make_client):
    fake = FakeSync(statuses=["PROCESSING"])
    client = make_client(fake, timeout_seconds=0.05, poll_interval_seconds=0.02)
    response = client.post("/sync", files={"video": VIDEO, "audio": AUDIO})

    assert response.status_code == 504
    assert response.json()["generation_id"] == "gen-123"
    assert "Timed out" in response.json()["error"]
    assert fake.create_calls == 1


def test_output_download_failure(make_client, tmp_path):
    fake = FakeSync(download_status=403)
    response = make_client(fake).post("/sync", files={"video": VIDEO, "audio": AUDIO})

    assert response.status_code == 502
    assert response.json()["error"] == "Failed to download the generated video"
    assert list((tmp_path / "output").iterdir()) == []


def test_sync_authentication_error(make_client):
    fake = FakeSync(create_status=401, create_body={"message": "Invalid API key", "error": "Unauthorized", "statusCode": 401})
    response = make_client(fake).post("/sync", files={"video": VIDEO, "audio": AUDIO})

    assert response.status_code == 502
    assert response.json()["error"] == "Sync.so authentication failed; check SYNC_API_KEY"
    assert "test-key" not in response.text
    assert fake.poll_calls == 0


def test_sync_validation_error(make_client):
    fake = FakeSync(create_status=400, create_body={
        "message": "Unsupported model", "error": "Bad Request", "statusCode": 400, "errorCode": "invalid_model",
    })
    response = make_client(fake).post("/sync", files={"video": VIDEO, "audio": AUDIO})

    assert response.status_code == 422
    assert response.json()["error"] == "Sync.so rejected the request: Unsupported model (invalid_model)"
