# speech-engine — Track A feasibility harness

NextBerry Vernacular, Track A: speech-to-text, timing, script adaptation and
expressive TTS. This repo is the **feasibility harness** — scripts that measure
what the providers actually do, and the findings those measurements produced.
It is not the production service.

Findings live in [`feasibility/REPORT.md`](feasibility/REPORT.md) and the model
inventory in [`feasibility/MODELS.md`](feasibility/MODELS.md). Read those first;
the code exists to produce and re-produce them.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env     # then paste your keys into .env (gitignored)
```

Needs `ffmpeg` on PATH (`brew install ffmpeg`); otherwise the bundled
`imageio-ffmpeg` binary is used.

Keys: `CAMB_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, optionally
`ANTHROPIC_API_KEY`. Google Cloud STT uses `gcloud auth print-access-token`.

## The blocks

Each script answers one question and writes raw results to
`feasibility/results/` (gitignored — they contain audio and client material).

| script | question |
|---|---|
| `block_a_camb_stt.py` | Camb STT: segments, diarization, pause structure |
| `block_a_gemini_stt.py` | Gemini: transcript quality, timestamp granularity |
| `block_a_gcp_stt.py` | Cloud STT v2: word offsets, per model and language |
| `block_d_tts.py` | TTS: duration control, rate lever, variance, rate calibration |
| `block_g_adapt_openrouter.py` | Stage 5 adaptation across LLM families |
| `block_h_e2e.py` | adapted copy → speech → does it land in the slot? |
| `block_i_granularity.py` | render per line or per utterance? |
| `block_j_assemble.py` | place every line on the timeline → one voice track |
| `block_j_refit.py` | re-fit an existing run: squeeze to slot, or borrow the gap |

Shared modules: `adapt_common.py` (akshara counting, rate model, fit classes),
`adapt_group.py` (utterance-level rewriting, protected terms, meaning checks),
`dryrun.py`, `ledger.py`.

## Two rules the harness enforces

**Dry-run before spending.** `--dry-run` builds every prompt against a fake
model, prints them, audits the wiring (placeholders needed vs values passed, and
values passed the template never uses) and forecasts cost. Three expensive bugs
were found this way after being missed in paid runs.

```bash
.venv/bin/python feasibility/blocks/block_g_adapt_openrouter.py \
    --segments feasibility/results/<stt output>.json --target pa --dry-run
```

**Every paid call is logged.** Adapters append to `feasibility/results/ledger.jsonl`:
Camb credits from `X-Credits-Required`, Gemini token counts, OpenRouter measured
USD and the serving provider. Totals:

```bash
.venv/bin/python feasibility/blocks/ledger.py            # everything
.venv/bin/python feasibility/blocks/ledger.py <RUN_ID>   # one run
```

Set `RUN_ID` per attempt, not per experiment, or two attempts merge into one row.

## A typical pass

```bash
# 1. timing contract from the source audio
.venv/bin/python feasibility/blocks/block_a_camb_stt.py path/to/audio.wav --language en-IN --mode slow

# 2. adapt into the target language (dry-run first)
.venv/bin/python feasibility/blocks/block_g_adapt_openrouter.py \
    --segments feasibility/results/<stt>.json --target pa --rate 5.27 \
    --protected "Brand,Product" --dry-run

# 3. speak it and place it on the timeline
.venv/bin/python feasibility/blocks/block_j_assemble.py \
    --adaptation feasibility/results/<adapt>.json --model gemini:gemini-3.8-flash-tts \
    --duration 30.189 --video path/to/video.mp4

# 4. fix overflow two ways and compare
.venv/bin/python feasibility/blocks/block_j_refit.py --run feasibility/results/<run> --mode borrow
.venv/bin/python feasibility/blocks/block_j_refit.py --run feasibility/results/<run> --mode squeeze
```

## What is deliberately not here

No fixtures and no results: they are client uploads and downloaded third-party
ads, and they are large. Point the scripts at your own media.

`--rate` defaults are measured per engine and language (Punjabi: Camb
`mars-instruct` 5.12 aksharas/sec, Gemini `3.8-flash-tts` 5.27). They are not
transferable — re-measure with `block_d_tts.py --test calibrate` for any new
engine, voice or language.
