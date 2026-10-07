# Advertisement Video Localization

Upload an ad video, choose a target Indian language, and get back a lip-synced localized video.

```
Upload ─► Input ─► STT ─► Translation ─► TTS ─► Lip-sync ─► Output
          (split)  └──── intermediate engine ────┘ (Sync.so)  (verify + record)
```

| Stage | What it does | Code | Origin |
|-------|--------------|------|--------|
| Input | Validate, store and split the ad into silent `video.mp4` and `audio.m4a` | `app/pipeline/input/` | `Prateek/` |
| Intermediate | Speech-to-text, then translation, then text-to-speech | `app/pipeline/intermediate/` (contract) and `Ishnit/` (implementation, pending) | `Ishnit/` |
| Output | Lip-sync the silent video to the localized audio via Sync.so | `app/pipeline/output/` | `Swati/` |
| UI | Upload, choose language, progress, results | `app/ui/streamlit_app.py` | new |

## Repository layout

```text
.
├── streamlit_app.py              # UI entry point: streamlit run streamlit_app.py
├── app/
│   ├── config/
│   │   ├── settings.py           # env-driven settings (paths, FFmpeg, Sync.so, engine selection)
│   │   └── languages.py          # the single list of supported languages
│   ├── pipeline/
│   │   ├── orchestrator.py       # localize_video(): runs every stage, reports progress
│   │   ├── models.py             # Stage, StageStatus, InputResult, OutputResult, LocalizationResult
│   │   ├── errors.py             # PipelineError ("STT failed: ..."), EngineError
│   │   ├── input/                # process_input() + FFmpeg/FFprobe services       (from Prateek/)
│   │   ├── intermediate/
│   │   │   ├── contract.py       # LocalizationEngine + request/response data types
│   │   │   └── ishnit_engine.py  # adapter to Ishnit/ (the only file that will import it)
│   │   └── output/               # generate_localized_output() + Sync.so client  (from Swati/)
│   ├── ui/streamlit_app.py       # presentation only; calls localize_video()
│   ├── api/                      # optional FastAPI: /extract, /sync, /health … (both original APIs)
│   ├── dev/                      # DEVELOPMENT-ONLY mocks (engine + lip-sync), opt-in via env
│   └── logging_config.py
├── Ishnit/                       # STT/TTT/TTS implementation (external; not modified here)
├── tests/                        # input/, output/, pipeline/, ui/
├── samples/output_stage/         # Swati's sample silent video + Malayalam audio
├── docs/                         # input-stage.md, output-stage.md
└── data/                         # generated runs (git-ignored)
```

Every localization run gets one folder, `data/runs/<video-name>/<timestamp>/`, containing
`input/` (original upload), `output/` (silent video and audio), `intermediate/` (transcript,
translation, TTS audio), `localized/` (final video), `metadata.json` (input stage) and
`localization.json` (status of every stage, artifacts, error).

## Setup

Requires Python 3.11+ and FFmpeg (with `libx264`). Install FFmpeg with `brew install ffmpeg`,
`apt install ffmpeg` or `winget install Gyan.FFmpeg`.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then edit; at minimum set SYNC_API_KEY for real lip-sync
```

## Run

```bash
streamlit run streamlit_app.py                     # UI at http://localhost:8501
uvicorn app.api.main:app --reload --port 8000      # optional HTTP API, docs at /docs
pytest -q                                          # tests
ruff check . && ruff format --check .              # lint
```

**Development mode without Ishnit's engine or a Sync.so key:**

```bash
LOCALIZATION_ENGINE=mock LIPSYNC_PROVIDER=mock streamlit run streamlit_app.py
```

The mocks are clearly flagged in the UI. The mock engine reuses the original audio (no
transcription or translation), and the mock lip-sync swaps the audio track without lip-sync.

## Configuration

All settings come from environment variables, or from `.env` in the repository root. See
`.env.example` for the full list.

| Group | Variables |
|-------|-----------|
| Paths | `DATA_DIR` (default `./data`), `RUNS_DIR` (default `./data/runs`) |
| Pipeline selection | `LOCALIZATION_ENGINE` = `mock` (default until Ishnit is integrated) or `ishnit`; `LIPSYNC_PROVIDER` = `sync` (default) or `mock` |
| Input stage | `FFMPEG_BINARY`, `FFPROBE_BINARY`, `MAX_UPLOAD_SIZE_MB` (2048), `FFMPEG_TIMEOUT_SECONDS` (3600) |
| Output stage (Sync.so) | `SYNC_API_KEY`, `SYNC_MODEL` (`sync-3`), `SYNC_TIMEOUT_SECONDS` (600), `SYNC_POLL_INTERVAL_SECONDS` (5), `SYNC_OUTPUT_DIR` (standalone API only) |
| Misc | `LOG_LEVEL` |

Languages live in `app/config/languages.py`: Hindi, English, Telugu, Malayalam, Kannada,
Tamil, Punjabi and Bengali. Each has an ISO 639-1 code, a display name and a BCP 47 locale.
Adding a language there makes it available everywhere.

The Streamlit upload limit (`.streamlit/config.toml`, 1024 MB) is separate from
`MAX_UPLOAD_SIZE_MB`.

## Using the pipeline from code

```python
from pathlib import Path
from app.pipeline import localize_video, PipelineError

try:
    result = localize_video(Path("ad.mp4"), target_language="hi", source_language="en")
    print(result.output.localized_video)
except PipelineError as err:
    print(err)  # e.g. "Translation failed: <reason>"
    print(err.stage, err.result.run_dir if err.result else None)
```

`source_language=None` asks the STT engine to detect the language. You can also call the
stages individually:

- `app.pipeline.input.process_input(file_obj, filename, settings.input)`
- `app.pipeline.output.generate_localized_output(silent_video, audio, output_dir, settings=...)`

Errors always name the failed stage: `Input processing failed`, `STT failed`,
`Translation failed`, `TTS failed`, `Video generation failed` or `Output generation failed`.
Each failure is logged with the `run_id` and written to `localization.json`.

## Integrating Ishnit's STT → TTT → TTS

The rest of the system depends only on `app/pipeline/intermediate/contract.py`:

```python
class LocalizationEngine(ABC):
    def transcribe(self, request: IntermediateRequest) -> Transcript: ...
    def translate(self, transcript: Transcript, request: IntermediateRequest) -> Translation: ...
    def synthesize(self, translation: Translation, request: IntermediateRequest) -> SynthesizedAudio: ...
```

`IntermediateRequest` provides:
- `source_audio`: `audio.m4a`, which may contain several tracks
- `silent_video`
- `source_language`: a `Language`, or `None` for auto-detect
- `target_language`
- `work_dir`: a per-run folder for the engine's files
- `run_id` and `audio_duration_seconds`

`SynthesizedAudio.path` must be the complete final soundtrack, in a Sync.so-compatible format
and under 20 MB.

To plug it in:
1. Implement the three methods in `app/pipeline/intermediate/ishnit_engine.py`, calling into
   `Ishnit/`. That file is the only place that imports from `Ishnit/`.
2. Raise `EngineError("readable reason")` on failure. The UI shows it as `STT failed: …` and so on.
3. Run with `LOCALIZATION_ENGINE=ishnit`.

No UI, orchestrator or output-stage changes are needed. `tests/pipeline/test_orchestrator.py`
shows a custom engine running through the whole pipeline.
