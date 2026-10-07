# Input stage — video/audio separation

> Originally `Prateek/` (the "Input-state" service). Code now lives in `app/pipeline/input/`;
> setup and running are covered in the [root README](../README.md).

Splits an uploaded ad video into:

1. **`video.mp4`** — the original video stream with **no audio**
2. **`audio.m4a`** — the **complete** audio (music, dialogue, background, every audio track)

This stage only separates streams with FFmpeg/FFprobe. It does no transcription, enhancement or
AI processing, and nothing is deliberately compressed. Streams are copied bit for bit whenever the
target container allows it; otherwise they are re-encoded once at high quality.

## Interfaces

| Caller | Entry point |
|--------|-------------|
| Pipeline / scripts | `app.pipeline.input.process_input(file_obj, filename, settings.input) -> InputResult` |
| HTTP (optional) | `POST /extract`, `GET /download/{run_id}/video\|audio`, `GET /runs/{run_id}`, `GET /health`, `GET /health/dependencies` |

**Supported input formats:** `.mp4`, `.mov`, `.avi`, `.mkv`. The extension check is
case-insensitive, and the content is also checked with FFprobe.

## Run directory

Each upload gets `<RUNS_DIR>/<sanitized-name>/<timestamp>/`, by default
`data/runs/<name>/<YYYYMMDD_HHMMSS_micro>/`. `run_id` is `<sanitized-name>__<timestamp>`.

```text
data/runs/Summer_Sale/20261008_103015_123456/
├── input/original_video.mp4   # original upload, byte for byte (keeps its extension)
├── output/
│   ├── video.mp4              # silent video (this stage)
│   └── audio.m4a              # all audio tracks (this stage)
├── metadata.json              # this stage's record (FFprobe info, strategies, commands, errors)
├── intermediate/              # STT/TTT/TTS artifacts (later stages)
├── localized/                 # lip-synced result (output stage)
└── localization.json          # whole-pipeline record (orchestrator)
```

**Filename sanitising:**
- Client-supplied directories are dropped (`../../x.mp4` becomes `x`).
- The name is folded to ASCII, and any character outside `[A-Za-z0-9._-]` becomes `_`.
- Leading and trailing dots are trimmed, the name is capped at 100 characters, and `video` is used if nothing is left.
- Every resolved path is checked to stay inside `RUNS_DIR`.

## How processing works

```text
upload ──► streamed to input/original_video.<ext>  (1 MB chunks, size-limited)
       ──► ffprobe -show_format -show_streams        (reject unreadable / no video / no audio)
       ──► video:  ffmpeg -map 0:<video> -c:v copy -an -sn -dn -f mp4 video.mp4
       ──► audio:  ffmpeg -map 0:<a1> -map 0:<a2> … -c:a copy|aac -vn -f ipod audio.m4a
       ──► ffprobe each output and verify ─► metadata.json
```

**Video (`video.mp4`)**
- Only the primary video stream is mapped. Cover art is ignored, and audio, subtitles and data are dropped (`-an -sn -dn`).
- **Stream copy** when the codec is MP4-safe: H.264, HEVC (tagged `hvc1`), AV1, MPEG-4 Part 2, VP9.
- Other codecs, or a copy that fails verification, are **re-encoded** to H.264
  (`libx264 -preset veryfast -crf 18 -pix_fmt yuv420p`, with dimensions rounded to even numbers).

**Audio (`audio.m4a`)**
- **Every audio stream is kept as its own track.** Nothing is mixed down or dropped, and channel
  layouts and language tags are kept.
- AAC/ALAC tracks are **stream-copied**. Other codecs are encoded to AAC at 192 kb/s for stereo,
  or 64 kb/s per channel for surround.

**Verification** (a failure triggers the fallback strategy, then an error):
- `video.mp4` has at least one video stream and **zero** audio streams.
- `audio.m4a` has exactly as many audio streams as the input and no video.
- The audio duration matches the input's within `max(0.5 s, 2 %)`.

If any step fails, everything in `output/` is deleted and the failure is written to
`metadata.json`. The original upload is kept for debugging. All FFmpeg/FFprobe calls use
argument lists via `subprocess.run` (no shell) with a timeout.

## Errors

The pipeline reports these as `Input processing failed: <message>`. The HTTP API returns
`{"detail": "<message>"}`.

| HTTP | When |
|------|------|
| 400 | Unsupported extension: `Unsupported video format. Supported formats: mp4, mov, avi, mkv` |
| 400 | Empty upload, or FFprobe can't read the file: `Uploaded file is not a valid or readable video file` |
| 400 | No video stream: `Uploaded file contains no video stream` |
| 400 | No audio stream: `Uploaded video has no audio stream, so there is no audio to extract` |
| 413 | Upload larger than `MAX_UPLOAD_SIZE_MB` |
| 500 | FFmpeg failed or its output failed verification. FFmpeg's stderr goes to logs and `metadata.json`, never to API clients |

## Limitations

- Multiple audio streams become multiple tracks in one `audio.m4a`, so simple players only play track 1.
- Only the primary video stream is carried into `video.mp4`.
- Non-AAC/ALAC audio is transcoded to AAC, which is lossy.
- iPhone-style edit lists: the copied audio can expose a few ms of encoder priming at the start.
  The audio content itself is unchanged.
- Run folders are never deleted automatically.
