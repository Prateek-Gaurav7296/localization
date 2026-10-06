# Sync.so lipsync service

A small FastAPI service. It takes a video and an audio file, runs a Sync.so `sync-3` lipsync generation, waits for it to finish, and saves the generated video locally.

## Architecture

```
Client
  |
  | video + audio (multipart/form-data)
  v
FastAPI  POST /sync
  |
  | POST https://api.sync.so/v2/generate   (files uploaded directly, model=sync-3)
  v
Sync.so sync-3
  |
  | poll GET https://api.sync.so/v2/generate/{id}  until COMPLETED / FAILED / REJECTED / timeout
  v
Generated video (outputUrl)
  |
  | download
  v
Local output/<generation_id>.mp4
```

The request stays open until the generation finishes. The service does not retry: if Sync.so returns an error, the generation fails, or the download fails, the request fails.

Direct file uploads to Sync.so must be **under 20MB per file**. Larger files are rejected with HTTP 413.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then set SYNC_API_KEY in .env
```

## Configuration

Set these in `.env` or as environment variables:

```
SYNC_API_KEY=...                 # required
SYNC_MODEL=sync-3
SYNC_TIMEOUT_SECONDS=600         # max time to wait for a generation
SYNC_POLL_INTERVAL_SECONDS=5
SYNC_OUTPUT_DIR=output
```

## Run

```bash
.venv/bin/uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

## Try it

```bash
curl -X POST "http://localhost:8000/sync" \
  -F "video=@input_video_audio/Input_Video_For_Sync.mov" \
  -F "audio=@input_video_audio/Input_Malayam_Audio_For_Sync.mp3"
```

Success response:

```json
{"status": "completed", "generation_id": "<id>", "output_file": "output/<id>.mp4"}
```

The generated video is saved at `output/<generation_id>.<ext>`, relative to the directory you started the server from. The extension comes from Sync's output URL and is usually `.mp4`.

Error response (4xx/5xx):

```json
{"status": "failed", "generation_id": "<id or null>", "error": "..."}
```

| Case | HTTP status |
| --- | --- |
| Missing `video` / `audio` field | 422 |
| Empty file / unsupported extension | 400 |
| File over 20MB | 413 |
| Sync.so rejected input (400/422) | 422 |
| Sync.so auth failure, generation FAILED/REJECTED, download failure, other Sync errors | 502 |
| Generation did not finish within `SYNC_TIMEOUT_SECONDS` | 504 |

Supported formats (from Sync.so docs): video `.mp4 .mov .qt .webm .avi`; audio `.wav .mp3 .ogg .flac .alac .mp4 .wma .m4a .m3a .aac`.

## Tests

The tests mock Sync.so, so they make no real API calls:

```bash
.venv/bin/pytest -q
```
