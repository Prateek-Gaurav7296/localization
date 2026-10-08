# Samples — one input/output per test

Small, committable evidence for the claims in [`../REPORT.md`](../REPORT.md).
Audio is re-encoded to 48 kbps mono MP3 (the findings are about *timing and
voice*, which survive that). Full-resolution runs stay out of git.

| file | what it shows |
|---|---|
| `01_stt/camb_slow_livpure_hi-IN.json` | Camb `slow` output: segments, speakers, no word timings (A1) |
| `01_stt/gemini_3.8_flash_verbatim.json` | Gemini transcript, Hinglish transcribed not translated (A4) |
| `02_tts_duration/duration_ignored_base.mp3` | rendered with no duration requested |
| `02_tts_duration/duration_requested_1.0s.mp3` | **1.0s requested — same ~6s of audio.** The duration parameter is a no-op (D1) |
| `02_tts_duration/rate_0.8_slow.mp3` / `rate_1.2_fast.mp3` | the `speaking_rate` lever working on `mars-8.1-pro-beta` (D4) |
| `02_tts_duration/variance_results.csv` | 5 identical renders per model; run-to-run spread (D2) |
| `03_adaptation/input_timing_contract.json` | the input: timed source lines |
| `03_adaptation/v1_protected_not_in_prompt.json` | trademarks transliterated — `Soar` → `ਸੋਅਰ` → glosses as "Sour" (G) |
| `03_adaptation/v3_protected_in_prompt.json` | trademarks kept 9/9, but the brand inserted into 4 lines that never said it |
| `03_adaptation/v4_brandfree_context.json` | brand removed from the context brief → 0 insertions |
| `04_end_to_end/fit_vs_budget.csv` | budget vs predicted vs actually spoken, per line (H) |
| `05_assembly/raw_no_fix.mp3` | lines placed at their original offsets, overflowing |
| `05_assembly/fix_a_gap_borrowing.mp3` | overflow allowed to run into the silence that follows |
| `05_assembly/fix_b_squeeze_to_slot.mp3` | every line time-stretched into its slot instead |
| `05_assembly/placement_report.json` | per-line slot, spoken length, status |

The two fixes in `05_assembly` are the same script and the same renders — only
the overflow strategy differs. Borrowing needed 1 line stretched past the 8%
quality cap; squeezing needed 7, one at double speed.

## 06_preview_video — watchable previews

Downscaled to 360p/480p at 48 kbps mono audio (~700 KB each). The picture is
untouched by the pipeline; only the voice track changed.

| file | what it shows |
|---|---|
| `livpure_camb_mars-instruct.mp4` | Punjabi dub, Camb `mars-instruct` |
| `livpure_gemini_3.8_flash.mp4` | same script, Gemini `3.8-flash-tts` — compare the two engines on identical copy |
| `livpure_gemini_squeezed_to_fit.mp4` | every line time-stretched into its slot: 11/11 lines past the 8% quality cap, three above 70% |
| `lipstick_raw_overflow.mp4` | lines placed at their original offsets, 7/11 overflowing |
| `lipstick_fix_gap_borrowing.mp4` | overflow runs into the silence that follows — 1 line needed stretching |
| `lipstick_fix_squeeze.mp4` | every line squeezed into its slot instead — 7 lines past the cap |

The borrow/squeeze pair is the clearest single comparison in the repo: same
script, same renders, same total duration, only the overflow strategy differs.

## Not included

Source video and audio: client uploads and third-party ads. The MP3s here are
generated speech, not source material. Point the scripts at your own media —
see the root README.
