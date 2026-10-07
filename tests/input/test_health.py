from tests.helpers import requires_ffmpeg


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@requires_ffmpeg
def test_dependency_health_reports_ffmpeg(client):
    response = client.get("/health/dependencies")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["ffmpeg"]["available"] and body["ffmpeg"]["version"].startswith("ffmpeg version")
    assert body["ffprobe"]["available"] and body["ffprobe"]["version"].startswith("ffprobe version")


def test_dependency_health_degraded_when_missing(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FFMPEG_BINARY", "definitely-not-ffmpeg-xyz")
    get_settings.cache_clear()
    response = client.get("/health/dependencies")
    assert response.status_code == 503
    assert response.json()["ffmpeg"]["available"] is False
