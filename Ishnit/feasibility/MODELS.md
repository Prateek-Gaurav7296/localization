# Model inventory by stage — tested, testable, cost

Costs verified 3 Oct 2026 (OpenRouter catalogue, sync.so, Sarvam). Re-verify before Phase 2.
LLM costs are per **12-line 30s ad, per language, per model** (~12k in / 8k out tokens).
`:batch` variants on OpenRouter are ~50% cheaper and we are a batch product.

Legend: ✅ tested · ⚠️ partially tested · ❌ not tested · 🚫 ruled out

---

## Stage 1 — Check video usable
| Tool | Status | Cost |
|---|---|---|
| ffprobe (duration, fps, codec, frame count) | ✅ 3 ads probed | free |
| PySceneDetect (shot changes) | ❌ | free |
| MediaPipe Face Landmarker (one face, mouth visibility) | ❌ | free |

Found: video-stream vs container duration disagree (750 frames @25 = 30.000s vs 30.047s).

## Stage 2 — Split audio
| Model | Status | Cost |
|---|---|---|
| Demucs htdemucs / htdemucs_ft (local) | ❌ | free (CPU/GPU time) |
| Camb `audio_separation` endpoint | ❌ | credits |
| AudioShake DME (escalation) | ❌ | enterprise |

Found: silencedetect on the *mix* finds almost no gaps — separation is a hard prerequisite for Stage 3.

## Stage 3 — Timing contract (ASR + alignment + diarization)
| Model | Text | Segment timing | Word timing | Status | Cost |
|---|---|---|---|---|---|
| Camb STT `slow` | best of tested | sub-10ms, deterministic | none | ✅ | credits |
| Camb STT `fast` | worse (transliterates) | identical to slow | none | ✅ | credits |
| `gemini-3.5-transcribe` | good, transliterates | none | none | ✅ | free tier |
| `gemini-3.8-flash` | good, keeps Latin | untested (503) | untested | ⚠️ | free tier |
| Google Cloud STT v2 `chirp_3/chirp_2/long/short` | — | — | **enableWordTimeOffsets** | ❌ billing | free 60 min/mo, card required |
| WhisperX (wav2vec2 forced alignment, hi+en) | — | — | expected best | ❌ | free, local |
| Montreal Forced Aligner | — | — | — | ❌ | free, local |

Diarization: Camb ✅ (2 speakers correctly separated, 88/12% split).
**No provider tested so far returns word timestamps.**

## Stage 4 — Performance reading
| Model | Status | Cost |
|---|---|---|
| Parselmouth / librosa (pitch, energy, rate, stress) | ❌ | free, local |
| Gemini 3.x multimodal (video+audio → delivery note) | ❌ | ~$0.04/ad |
| GPT-5 / Claude vision | ❌ | $0.10–0.26/ad |

## Stage 5 — Script adaptation
**Generators** (per ad per language):
| Model | Status | Cost |
|---|---|---|
| `qwen3.8-27b:free`, `gemma-4-31b-it:free` | ❌ | **$0.000** |
| mistral-small-24b | ❌ | $0.001 |
| gpt-5-nano | ❌ | $0.004 |
| qwen3-235b-a22b | ❌ | $0.020 |
| gemini-3.8-flash | ⚠️ rate-limited | $0.039 (free tier avail) |
| gemini-3.5-flash | ⚠️ used as fallback | $0.090 (free tier avail) |
| claude-haiku-4.5 | ❌ | $0.052 |
| gpt-5 | ❌ | $0.095 |
| claude-sonnet-5 | ❌ no credits | $0.104 |
| gemini-3.1-pro-preview | ⚠️ 429s | $0.120 |
| claude-opus-5 | ❌ | $0.260 |
| Sarvam-M (Indic specialist, own key) | ❌ | own pricing |

**Back-translation glosser** (must be a different family; literal is a feature):
| Model | Status | Cost |
|---|---|---|
| IndicTrans3-beta (CC BY 4.0, 15 Indic incl. Odia/Punjabi/Assamese) | ❌ | free, local |
| IndicTrans2 (MIT, 22 languages) | ❌ | free, local |
| Camb `translation` (formality/age/gender + dictionaries) | ❌ | credits |
| NLLB-200 | 🚫 | CC-BY-NC — not usable commercially |

**Deterministic:** akshara counter ✅ built and verified (Kannada/Telugu/Devanagari/Gurmukhi/Odia).

## Stage 6 — TTS
| Model | Status | Cost | Notes |
|---|---|---|---|
| Camb `mars-pro` | ❌ | credits | 48kHz dubbing-grade |
| Camb `mars-instruct` | ❌ | credits | only model taking `user_instructions` |
| Camb `mars-flash` | ❌ | credits | low latency, likely irrelevant |
| Camb `mars-8.1-pro-beta` / `-flash-beta` | ❌ | credits | CMU phonemes, non-verbals |
| `gemini-3.8-flash-tts` | ❌ | free tier | |
| `gemini-3.8-flash-lite-tts` | ❌ | free tier | Google positions it for dubbing |
| `gemini-3.1-flash-tts-preview`, 2.5 pair | ❌ | free tier | |
| Sarvam Bulbul v3/v4 | ❌ | ~₹30/10k chars | won a 20k-vote blind Indic eval |
| Azure neural Indic | ❌ | pay-go | the only solid **Odia** path |
| AI4Bharat IndicTTS | ❌ | free, local | Odia + Assamese |

**Camb knobs found in SDK:** `speaking_rate`, `user_instructions`, `enhance_named_entities_pronunciation`,
`inference_options{stability,temperature,speaker_similarity}`, and **`output_configuration.duration`** —
which contradicts the doc's claim that no duration parameter exists. Highest-value untested item.

## Stage 7 — Mix
| Tool | Status | Cost |
|---|---|---|
| FFmpeg + numpy (placement, ducking, loudness) | ❌ | free |

## Stage 8 — Lip-sync (Dev B)
| Model | Status | Cost |
|---|---|---|
| sync-3 (4K native, obstruction detection) | ❌ | **$0.133/s = $4.00 per 30s** |
| lipsync-2-pro (512×512) | ❌ | ~$0.05/s = $1.50 per 30s |
| lipsync-2 | ❌ | ~$0.04/s = $1.20 per 30s |
| LatentSync / MuseTalk / KeySync (local) | ❌ | free, needs GPU |

## Stage 9 — Assemble & verify
| Tool | Status | Cost |
|---|---|---|
| FFmpeg/ffprobe, 11 blocking checks | ⚠️ frame/duration checks done ad-hoc | free |

## Stage 10 — Deliver
| Tool | Status | Cost |
|---|---|---|
| Storage, naming, audit log | ❌ | negligible |

---

## Language coverage gaps (the two that matter)

| | Camb TTS | Camb translate/STT | Gemini TTS | Azure | AI4Bharat |
|---|---|---|---|---|---|
| **Odia** | ❌ | ❌ | Preview | ✅ | ✅ |
| **Assamese** | ✅ | ❌ | ❌ | ✅ | ✅ |

Odia has no Camb path at all. Assamese can be spoken by Camb but not translated or transcribed by it.
Text side for both is solved by IndicTrans2/3 (permissive licences).

## Spend plan

| Priority | Item | Amount |
|---|---|---|
| 1 | OpenRouter credits — whole Stage 5 bake-off, replaces the Anthropic account | $10 |
| 2 | Google Cloud billing — unlocks Cloud STT word offsets; 60 min/mo free covers Block A | ~$0 (card gate) |
| 3 | Camb credits — TTS auditions, Blocks B/C/D | as needed |
| 4 | sync.so — Phase 0 lip-sync, 5 ads × 2 models | ~$40–60 |

Never pay for: aligners, separation, back-translation, akshara counting — all local and free.
