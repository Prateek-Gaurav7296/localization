"""Minimal client for the Sync.so generation API.

Endpoints used (https://sync.so/docs/api-reference):
  POST /v2/generate       multipart/form-data with `model`, `video`, `audio` file fields
  GET  /v2/generate/{id}  generation status; `outputUrl` is set once COMPLETED
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

SYNC_API_BASE_URL = "https://api.sync.so"

# Status values documented for the Generation object.
IN_PROGRESS_STATUSES = {"PENDING", "PROCESSING"}
FAILED_STATUSES = {"FAILED", "REJECTED"}
COMPLETED_STATUS = "COMPLETED"


class SyncError(Exception):
    """A failure talking to Sync.so. `http_status` is what our API returns to the client."""

    def __init__(self, message: str, http_status: int = 502, generation_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.generation_id = generation_id


@dataclass
class MediaFile:
    filename: str
    content: bytes
    content_type: str


@dataclass
class SyncResult:
    generation_id: str
    output_file: Path


class SyncService:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
        output_dir: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.output_dir = Path(output_dir)
        self._api = httpx.Client(
            base_url=SYNC_API_BASE_URL,
            headers={"x-api-key": api_key},
            timeout=httpx.Timeout(120.0, connect=10.0),
            transport=transport,
        )
        # The output URL is a pre-signed URL; the API key must not be sent to it.
        self._download = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._api.close()
        self._download.close()

    def run(self, video: MediaFile, audio: MediaFile) -> SyncResult:
        generation_id = self.create_generation(video, audio)
        output_url = self.wait_for_completion(generation_id)
        output_file = self.download_output(generation_id, output_url)
        return SyncResult(generation_id=generation_id, output_file=output_file)

    def create_generation(self, video: MediaFile, audio: MediaFile) -> str:
        logger.info("Creating Sync generation (model=%s)", self.model)
        response = self._request(
            "POST",
            "/v2/generate",
            data={"model": self.model},
            files={
                "video": (video.filename, video.content, video.content_type),
                "audio": (audio.filename, audio.content, audio.content_type),
            },
        )
        generation_id = self._json(response).get("id")
        if not generation_id:
            raise SyncError("Unexpected response from Sync.so: missing generation id")
        logger.info("Sync generation created: %s", generation_id)
        return generation_id

    def wait_for_completion(self, generation_id: str) -> str:
        """Poll the same generation until it finishes. Returns the output URL."""
        deadline = time.monotonic() + self.timeout_seconds
        last_status = None
        while True:
            response = self._request("GET", f"/v2/generate/{generation_id}", generation_id=generation_id)
            generation = self._json(response, generation_id)
            status = generation.get("status")
            if status != last_status:
                logger.info("Generation %s status: %s", generation_id, status)
                last_status = status

            if status == COMPLETED_STATUS:
                output_url = generation.get("outputUrl")
                if not output_url:
                    raise SyncError(
                        "Generation completed but Sync.so returned no outputUrl",
                        generation_id=generation_id,
                    )
                return output_url

            if status in FAILED_STATUSES:
                error = generation.get("error") or "no error message"
                logger.error(
                    "Sync generation %s %s: %s (errorCode=%s)",
                    generation_id,
                    status,
                    error,
                    generation.get("errorCode"),
                )
                raise SyncError(f"Sync generation {status.lower()}: {error}", generation_id=generation_id)

            if status not in IN_PROGRESS_STATUSES:
                raise SyncError(f"Unexpected generation status from Sync.so: {status!r}", generation_id=generation_id)

            if time.monotonic() + self.poll_interval_seconds > deadline:
                logger.error("Sync generation %s timed out after %ss", generation_id, self.timeout_seconds)
                raise SyncError(
                    f"Timed out after {self.timeout_seconds:g}s waiting for Sync generation (last status: {status})",
                    http_status=504,
                    generation_id=generation_id,
                )
            time.sleep(self.poll_interval_seconds)

    def download_output(self, generation_id: str, output_url: str) -> Path:
        suffix = Path(urlparse(output_url).path).suffix or ".mp4"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_file = self.output_dir / f"{generation_id}{suffix}"
        partial_file = output_file.with_name(output_file.name + ".part")

        logger.info("Downloading output for generation %s", generation_id)
        try:
            with self._download.stream("GET", output_url) as response:
                response.raise_for_status()
                with open(partial_file, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
            partial_file.replace(output_file)
        except (httpx.HTTPError, OSError) as exc:
            partial_file.unlink(missing_ok=True)
            logger.error("Output download failed for generation %s: %s", generation_id, exc)
            raise SyncError("Failed to download the generated video", generation_id=generation_id) from exc

        logger.info("Output saved: %s", output_file)
        return output_file

    def _request(self, method: str, url: str, generation_id: str | None = None, **kwargs) -> httpx.Response:
        try:
            response = self._api.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            logger.error("Network error calling Sync.so %s %s: %s", method, url, exc)
            raise SyncError("Could not reach Sync.so", generation_id=generation_id) from exc

        if response.is_success:
            return response

        detail = _error_detail(response)
        logger.error("Sync.so %s %s returned HTTP %s: %s", method, url, response.status_code, detail)
        if response.status_code in (401, 403):
            raise SyncError("Sync.so authentication failed; check SYNC_API_KEY", generation_id=generation_id)
        if response.status_code in (400, 422):
            raise SyncError(f"Sync.so rejected the request: {detail}", http_status=422, generation_id=generation_id)
        raise SyncError(f"Sync.so returned HTTP {response.status_code}: {detail}", generation_id=generation_id)

    @staticmethod
    def _json(response: httpx.Response, generation_id: str | None = None) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise SyncError("Unexpected response from Sync.so: body is not a JSON object", generation_id=generation_id)
        return body


def _error_detail(response: httpx.Response) -> str:
    """Pull the documented `message`/`errorCode` out of a Sync error body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or response.reason_phrase
    if not isinstance(body, dict):
        return str(body)[:300]
    message = body.get("message") or body.get("error") or "unknown error"
    if isinstance(message, list):
        message = "; ".join(str(m) for m in message)
    error_code = body.get("errorCode")
    return f"{message} ({error_code})" if error_code else str(message)
