# Output stage — Sync.so lip-sync

> Originally `Swati/syncLocaliztion/`. Code now lives in `app/pipeline/output/`;
> setup and running are covered in the [root README](../README.md).

Takes a video and an audio file, runs a Sync.so `sync-3` lip-sync generation, waits for it to
finish and saves the generated video locally.

```
silent video + localized audio
  |
  | POST https://api.sync.so/v2/generate   (files uploaded directly, model=sync-3)
  v
Sync.so sync-3
  |
  | poll GET https://api.sync.so/v2/generate/{id}  until COMPLETED / FAILED / REJECTED / timeout
  v
Generated video (outputUrl, pre-signed; the API key is never sent to it)
  |
  | download (to <file>.part, then renamed)
  v
<output_dir>/<generation_id>.mp4
```

The service does not retry: if Sync.so returns an error, the generation fails, or the download
fails, the call fails.

## Interfaces

| Caller | Entry point |
|--------|-------------|
| Pipeline / scripts | `app.pipeline.output.generate_localized_output(silent_video, localized_audio, output_dir, settings=...) -> OutputResult` |
| Low-level client | `app.pipeline.output.sync_service.SyncService(...).run(video: MediaFile, audio: MediaFile) -> SyncResult` |
| HTTP (optional) | `POST /sync` with multipart fields `video` and `audio` |

In the pipeline the result is saved to `<run_dir>/localized/<generation_id>.mp4`. The standalone
`POST /sync` saves to `SYNC_OUTPUT_DIR`.

Configuration: `SYNC_API_KEY` (required), `SYNC_MODEL=sync-3`, `SYNC_TIMEOUT_SECONDS=600`,
`SYNC_POLL_INTERVAL_SECONDS=5`, `SYNC_OUTPUT_DIR`. Set `LIPSYNC_PROVIDER=mock` to swap in a
development-only stand-in that replaces the audio track without lip-sync.

## Try it standalone

Sample inputs, a silent video and Malayalam audio, are in `samples/output_stage/`:

```bash
uvicorn app.api.main:app --reload
curl -X POST "http://localhost:8000/sync" \
  -F "video=@samples/output_stage/Input_Video_For_Sync.mov" \
  -F "audio=@samples/output_stage/Input_Malayam_Audio_For_Sync.mp3"
```

Success: `{"status": "completed", "generation_id": "<id>", "output_file": "data/sync_output/<id>.mp4"}`.
Error: `{"status": "failed", "generation_id": "<id or null>", "error": "..."}`.

| Case | HTTP status | Pipeline message |
| --- | --- | --- |
| Missing `video` / `audio` field | 422 | – |
| Empty file / unsupported extension | 400 | `Video generation failed: The video file is empty` … |
| File over 20MB | 413 | `Video generation failed: The video file is 57.0MB; Sync.so direct uploads must be under 20MB` |
| Sync.so rejected input (400/422) | 422 | `Video generation failed: Sync.so rejected the request: …` |
| Auth failure, generation FAILED/REJECTED, download failure, other Sync errors | 502 | `Video generation failed: …` |
| Generation did not finish within `SYNC_TIMEOUT_SECONDS` | 504 | `Video generation failed: Timed out after …` |

Supported formats (from Sync.so docs): video `.mp4 .mov .qt .webm .avi`; audio
`.wav .mp3 .ogg .flac .alac .mp4 .wma .m4a .m3a .aac`. Direct uploads must be **under 20MB per file**.
