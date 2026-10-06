# Input-state — video/audio separation service

A small FastAPI service that takes an uploaded video and splits it into:

1. **`video.mp4`** — the original video stream with **no audio**
2. **`audio.m4a`** — the **complete** audio (music, dialogue, background, every audio track)

It is a pure stream-separation pipeline built on FFmpeg/FFprobe. There is no transcription,
enhancement, AI processing, or deliberate compression. Streams are copied bit-for-bit
whenever the target container allows it; otherwise they are re-encoded once at high quality.

---

## 1. Requirements

| Tool    | Version      |
|---------|--------------|
| Python  | 3.11+        |
| FFmpeg  | 5.0+ (tested with 9.0.2), must include `libx264` and `aac` |
| FFprobe | ships with FFmpeg |

## 2. Installing FFmpeg

```bash
# macOS
brew install ffmpeg
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg
# Windows (winget)
winget install Gyan.FFmpeg
```

Check that both binaries are installed:

```bash
ffmpeg -version | head -1
ffprobe -version | head -1
```

Once the server is running, `GET /health/dependencies` reports the same information. The server
also logs the FFmpeg/FFprobe versions at startup, or an error if either is missing.

## 3. Python setup

```bash
cd Input-state
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # + pytest, httpx, ruff
```

(`uv venv --python 3.11 && uv pip install -r requirements-dev.txt` works too.)

## 4. Configuration

Settings come from environment variables. A `.env` file in the project root is loaded
automatically; copy `.env.example` to `.env` to customise.

| Variable                 | Default    | Meaning |
|--------------------------|------------|---------|
| `INPUT_ROOT`             | `./input`  | Where run folders are created (relative paths resolve against the project root) |
| `FFMPEG_BINARY`          | `ffmpeg`   | Name on `PATH` or absolute path |
| `FFPROBE_BINARY`         | `ffprobe`  | Name on `PATH` or absolute path |
| `MAX_UPLOAD_SIZE_MB`     | `2048`     | Larger uploads are rejected with HTTP 413 |
| `FFMPEG_TIMEOUT_SECONDS` | `3600`     | Maximum run time for any single FFmpeg call |
| `LOG_LEVEL`              | `INFO`     | Python logging level |
| `HOST` / `PORT` / `RELOAD` | `0.0.0.0` / `8000` / off | Used by `run.py` only |

## 5. Starting the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# or
python run.py
```

Interactive docs are served at <http://localhost:8000/docs>.

## 6. API endpoints

| Method | Path                          | Description |
|--------|-------------------------------|-------------|
| `GET`  | `/health`                     | Liveness: `{"status": "ok"}` |
| `GET`  | `/health/dependencies`        | FFmpeg/FFprobe availability and versions (503 if either is missing) |
| `POST` | `/extract`                    | Upload a video as `multipart/form-data` field `file` |
| `GET`  | `/download/{run_id}/video`    | Download `video.mp4` (`video/mp4`) |
| `GET`  | `/download/{run_id}/audio`    | Download `audio.m4a` (`audio/mp4`) |
| `GET`  | `/runs/{run_id}`              | The run's `metadata.json`, including failure details for failed runs |

**Supported input formats:** `.mp4`, `.mov`, `.avi`, `.mkv` (extension check is case-insensitive;
the content is also checked with FFprobe).

### Errors

Every error returns the body `{"detail": "<message>"}`.

| Status | When |
|--------|------|
| 400 | Unsupported extension: `Unsupported video format. Supported formats: mp4, mov, avi, mkv` |
| 400 | Empty upload, or a file FFprobe can't read (corrupt or truncated): `Uploaded file is not a valid or readable video file` |
| 400 | No video stream: `Uploaded file contains no video stream` |
| 400 | No audio stream: `Uploaded video has no audio stream, so there is no audio to extract` (no empty audio file is created) |
| 413 | Upload larger than `MAX_UPLOAD_SIZE_MB` |
| 422 | No `file` form field |
| 500 | FFmpeg failed or its output failed verification: `... Check server logs for details.` |

Responses never include stack traces or FFmpeg stderr. Those go to the server log, and to the
run's `metadata.json` under `error.detail`.

## 7. Example request

```bash
curl -X POST "http://localhost:8000/extract" -F "file=@sample.mp4"
```

### Example response

```json
{
  "status": "success",
  "run_id": "My_Sample_Video__20261004_010137_873780",
  "input_file": "input/My_Sample_Video/20261004_010137_873780/input/original_video.mp4",
  "video_file": "input/My_Sample_Video/20261004_010137_873780/output/video.mp4",
  "audio_file": "input/My_Sample_Video/20261004_010137_873780/output/audio.m4a",
  "metadata_file": "input/My_Sample_Video/20261004_010137_873780/metadata.json",
  "video_download_url": "http://localhost:8000/download/My_Sample_Video__20261004_010137_873780/video",
  "audio_download_url": "http://localhost:8000/download/My_Sample_Video__20261004_010137_873780/audio",
  "run_url": "http://localhost:8000/runs/My_Sample_Video__20261004_010137_873780",
  "video_duration_seconds": 10.0,
  "audio_duration_seconds": 10.0,
  "audio_track_count": 1,
  "video_strategy": "copy",
  "audio_strategy": "copy"
}
```

Download the outputs:

```bash
curl -OJ "http://localhost:8000/download/My_Sample_Video__20261004_010137_873780/video"
curl -OJ "http://localhost:8000/download/My_Sample_Video__20261004_010137_873780/audio"
```

## 8. Directory structure

Each upload gets `input/<sanitized-name>/<timestamp>/`. The timestamp is
`YYYYMMDD_HHMMSS_microseconds` (local time), and directory creation is atomic, so two runs can
never share a folder. `run_id` is `<sanitized-name>__<timestamp>`.

```text
Input-state/
├── app/
│   ├── main.py                  # FastAPI app, logging, upload-size guard
│   ├── config.py                # env-var settings
│   ├── errors.py                # domain errors → HTTP status
│   ├── routes/{extraction,health}.py
│   ├── schemas/extraction.py    # Pydantic response models
│   └── services/
│       ├── file_service.py      # sanitising, run dirs, streamed save, metadata
│       ├── media_service.py     # ffprobe inspection & validation
│       ├── ffmpeg_service.py    # ffmpeg commands, verification, fallbacks
│       └── extraction_service.py# the pipeline
├── input/
│   └── My_Sample_Video/
│       └── 20261004_010137_873780/
│           ├── input/original_video.mp4   # original upload, byte-for-byte
│           ├── output/
│           │   ├── video.mp4
│           │   └── audio.m4a
│           └── metadata.json
├── tests/
├── requirements.txt / requirements-dev.txt
├── .env.example
└── run.py
```

`original_video` keeps the uploaded extension (`.mov`, `.avi`, …).

**Filename sanitising:** the directory name is the uploaded file's stem, with any client-supplied
directories removed (`../../x.mp4` becomes `x`). It is folded to ASCII, every character outside
`[A-Za-z0-9._-]` becomes `_`, and leading/trailing dots are trimmed. Names are capped at 100
characters, and `video` is used if nothing is left. Every resolved path is also checked to stay
inside `INPUT_ROOT`.

### metadata.json

Every run writes `metadata.json`. It records the original filename, format, timestamp, output
paths, video and audio durations, the strategy used for each output, the exact FFmpeg commands
(with run-relative paths), a FFprobe summary of the input and both outputs, and the processing
time. Failed runs instead get `"processing_status": "failed"` and an `error` object with type,
stage, HTTP status, message, and internal detail.

## 9. How processing works

```text
upload ──► streamed to input/original_video.<ext>  (1 MB chunks, size-limited)
       ──► ffprobe -show_format -show_streams        (reject unreadable / no video / no audio)
       ──► video:  ffmpeg -map 0:<video> -c:v copy -an -sn -dn -f mp4 video.mp4
       ──► audio:  ffmpeg -map 0:<a1> -map 0:<a2> … -c:a copy|aac -vn -f ipod audio.m4a
       ──► ffprobe each output and verify ─► metadata.json
```

**Video (`video.mp4`)**
- Maps only the primary video stream. Embedded cover art is ignored. Audio, subtitles and data streams are dropped (`-an -sn -dn`).
- **Stream copy** when the codec is MP4-safe: H.264, HEVC (tagged `hvc1`), AV1, MPEG-4 Part 2, VP9.
- Other codecs (MJPEG, MPEG-2, …), or a copy that fails or doesn't verify, are **re-encoded** to
  H.264 (`libx264 -preset veryfast -crf 18 -pix_fmt yuv420p`, rounded to even dimensions).
- `+faststart` puts the index at the front of the file so it can start playing while downloading.

**Audio (`audio.m4a`)**
- **Every audio stream is kept as its own track in `audio.m4a`.** Nothing is mixed down or
  dropped, so a separate dialogue track and music track, or several language tracks, all survive
  with their channel layout (e.g. 5.1) and language tags. Most players only play the first
  track; see Limitations.
- AAC/ALAC tracks are **stream-copied**. Other codecs (PCM, MP3, Opus, AC-3, …) are encoded to
  AAC at 192 kb/s for stereo, or 64 kb/s per channel for surround (e.g. 384 kb/s for 5.1).
- `mixed` means AAC tracks were copied and the others encoded.

**Verification** (a failure triggers the fallback strategy, then HTTP 500):
- `video.mp4` has at least one video stream and **zero** audio streams.
- `audio.m4a` has exactly as many audio streams as the input, and no video.
- The audio duration matches the input's audio duration within `max(0.5 s, 2 %)`.

If any step fails, everything in `output/` is deleted and the failure is written to
`metadata.json`. The original upload is kept for debugging.

All FFmpeg/FFprobe calls use argument lists via `subprocess.run` (no shell) with a timeout.
stderr is captured and logged.

## 10. Testing

```bash
pytest -q          # 61 tests
ruff check . && ruff format --check .
```

The integration tests generate small clips with FFmpeg at session start, so no media files are
committed. They cover:

- MP4 (H.264+AAC, copy/copy), MOV (H.264+PCM), AVI (MJPEG+MP3, re-encode), AVI (MPEG-4+PCM)
- MKV (VP9 + Opus stereo + AAC 5.1, multi-track), MKV with odd dimensions + MPEG-2
- For each: HTTP 200, directory layout, original preserved byte-for-byte, and (via the exact
  `ffprobe -select_streams a|v` commands) video present and audio absent in `video.mp4`, every
  track present in `audio.m4a`, full durations, downloads, and `/runs`
- Rejections: `.txt`, empty file, random bytes, truncated MP4, text renamed to `.mp4`, video with
  no audio, audio-only file, oversized upload (both early and mid-stream)
- FFmpeg crash (500, partial output removed, no stderr leaked) and a missing FFmpeg binary
- Filename sanitising, path traversal, unique run folders, `run_id` lookup hardening

Tests that need FFmpeg are skipped automatically if it is not installed.

Manual check of an output:

```bash
ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 video.mp4   # prints nothing
ffprobe -v error -select_streams v -show_entries stream=index -of csv=p=0 video.mp4   # prints 0
ffprobe -v error -show_entries stream=codec_type,codec_name,duration -of csv=p=0 audio.m4a
```

## 11. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `500 Media processing tool is unavailable` / startup log `ffmpeg binary 'ffmpeg' not found` | Install FFmpeg, or set `FFMPEG_BINARY`/`FFPROBE_BINARY` to absolute paths. Check `GET /health/dependencies`. |
| `Form data requires "python-multipart"` | `pip install -r requirements.txt` |
| `413` on a large upload | Raise `MAX_UPLOAD_SIZE_MB`. A reverse proxy in front may have its own limit (e.g. nginx `client_max_body_size`). |
| `500 Video extraction failed` | Read `GET /runs/{run_id}`: `error.detail` contains FFmpeg's stderr. The server log lines are tagged `[run_id=…]`. |
| Re-encode fails with `Unknown encoder 'libx264'` | Your FFmpeg build lacks libx264. Install a full build (`brew install ffmpeg`, `apt install ffmpeg`). |
| Long files time out | Raise `FFMPEG_TIMEOUT_SECONDS`. |
| A player plays only one audio language | `audio.m4a` contains every track; choose the track in the player, or see the track list with `ffprobe`. |

## 12. Limitations and assumptions

- **Multiple audio streams** become multiple tracks in one `audio.m4a`, not a single mixed track.
  This is lossless and faithful to the source, but simple players only play track 1.
- Only the **primary video stream** goes into `video.mp4`. Additional video angles, subtitles,
  chapters-as-streams and data tracks are not carried over.
- Non-AAC/ALAC audio is transcoded to AAC, which is lossy. Lossless sources such as PCM/FLAC
  therefore lose bit-exactness. This is the price of the `.m4a` + broad-compatibility requirement.
- Processing is synchronous: the request returns once both files are done. Copy-path processing
  takes well under a second for short clips, but very large files that need re-encoding take as
  long as the encode does. For heavy workloads, put a job queue in front.
- Starlette spools multipart uploads to a temp file before the handler runs. The upload is then
  copied in chunks into the run folder, so RAM use stays flat, but peak disk use is about twice
  the upload size.
- Run folders are never deleted automatically. Prune `input/` as needed.
- Failed runs keep their folder (`input/` and `metadata.json`) for debugging. Uploads with an
  unsupported extension are rejected before any folder is created.
