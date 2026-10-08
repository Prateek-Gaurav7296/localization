# Track A feasibility — findings

Updated 3 Oct 2026. Fixtures: `IT0AfU3277I` (Livpure Smart RO, 30.047s, 1080p25, Hinglish)
for Block A; generated Punjabi/Hindi corpora (`fixtures/corpus_pa.json`, `corpus_hi.json`)
for Block D. No hand-timed ground truth (F2) yet, so no boundary-error numbers.

Legend: ✅ answered · ⚠️ partial · ❌ open

---

## Block A — STT, timing, diarization

| Provider | Text | Segment timing | Word timing | Diarization | Latency |
|---|---|---|---|---|---|
| Camb STT `slow` | best tested | sub-10ms, deterministic | none | ✅ | 87s / 30s audio |
| Camb STT `fast` | worse — transliterates, mangles fillers | identical to slow | none | ✅ | 72s |
| `gemini-3.5-transcribe` | good, transliterates English → Devanagari | none | none | ❌ | ~5s |
| `gemini-3.8-flash` | good, keeps English in Latin | untested (503) | untested | ❌ | ~10s |
| Google Cloud STT v2 | — | — | — | — | blocked on billing |

**A1. No tested provider returns word-level timestamps.** Camb ignores
`word_level_timestamps=True`; `gemini-3.5-transcribe` ignores prompts entirely and returns
text only. Google Cloud STT (`enableWordTimeOffsets`) is untested — billing.

**A2. Segment timings are deterministic; text is not.** Camb `fast` and `slow` produced
byte-identical boundaries (segmentation/VAD is shared, the mode only swaps the text model)
but 7 of 12 segments differ in text.

**A3. Script choice for English words is arbitrary per run.** In one segment "free" appeared
as `फ्री` then `free` in slow, and `free` then `फ्री` in fast — same two words, swapped.
→ The Stage 6 round-trip check must transliterate both sides to a canonical form and strip
case and punctuation. Raw string comparison would flag correct lines. Use CER, not WER:
Dravidian languages are agglutinative and WER punishes suffix differences.

**A4. Use `slow`.** Fast saved 15s and lost quality: transliterated English, a
mis-transcribed filler (`म्हूं-हूं` → `नहीं`), a changed verb form (`बनाएंगे` → `बनाएँ`).

**A5. Segments are mostly usable as Lines.** 11 of 12 fall between 0.3s and 3.4s. One is 6.0s
and holds three sentences — split those with VAD on the separated voice stem (no aligner needed).

**A6. Hinglish, not Hindi.** About a third of the ad is English inside Hindi sentences.
The spec's "source: English or Hindi" understates the ASR problem.

**A7. Two voices: on-screen presenter (88.1%, 16.94s) + off-screen endline VO (11.9%, 2.29s).**
Decision taken: **accept**. Stage 1 rejects only on more than one *on-screen* speaker.
Track A therefore owes Track B two tracks: `dub_dialogue_full` (mix) and `dub_lipsync_drive`
(on-screen speaker only, off-screen lines silent).

**A8. 32% of the ad is pause** — 9.50s of 30.05s across 11 gaps. Measured elasticity budget
for gap-borrowing.

**A9. Video vs container duration disagree.** 750 frames @25fps = exactly 30.000s; container
reports 30.047s. The timing contract must take duration from the video stream, or DoD checks
M1 and M2 contradict each other.

---

## Block D — TTS duration control (Punjabi, then Hindi)

**D1. `output_configuration.duration` is ignored.** Requested 1.0 / 1.5 / 2.0 / 3.0 / 4.0s on
`mars-instruct`; output stayed 6.0–7.2s throughout, and the 4.0s request came back *shorter*
than the 1.0s one. The spec's premise holds: **no duration guarantee, the fit ladder stays.**

**D2. Run-to-run variance exceeds the <2% threshold for every model and language tested.**
Same text, 5 identical renders:

| model | language | mean | sd | CV | spread |
|---|---|---|---|---|---|
| mars-8.1-pro-beta | Punjabi | 6.754s | 0.071 | **1.1%** | 2.9% |
| mars-instruct | Punjabi | 6.491s | 0.348 | 5.4% | 16.1% |
| mars-8.1-pro-beta | Hindi | 8.145s | 0.315 | 3.9% | 10.7% |
| mars-instruct | Hindi | 6.073s | 0.217 | 3.6% | 9.5% |
| gemini-3.8-flash-tts | Punjabi | 6.91s | 0.44 | 6.4% | 17.4% |

→ **Predict-then-render is dead. Stage 6 must render, measure, and loop.** `generate()`
returning *measured* duration is load-bearing, not a nicety. An outlier rule is also needed
(mars-instruct produced 7.11s against a 6.06s sibling on identical input).
Caveat: n=5. Spread (max−min) is noisy at that sample size; CV is the better statistic.
Repeat at n=10 before quoting these as final.

**D3. Variance does not transfer across languages.** `8.1-pro-beta` looked 5× steadier than
`mars-instruct` in Punjabi and is level with it in Hindi. Consistency must be measured per
(model × language × voice) and belongs in the Language Pack beside the rate model.

**D4. `speaking_rate` is the only working lever, and only on one model.**

| rate | 8.1-pro-beta | vs 1.0 | ideal | mars-instruct |
|---|---|---|---|---|
| 0.8 | 8.409s | +24.2% | +25.0% | 6.592s |
| 0.9 | 7.441s | +9.9% | +11.1% | 6.676s ← slower setting, shorter audio |
| 1.0 | 6.768s | — | — | 6.347s |
| 1.1 | 6.204s | −8.3% | −9.1% | 6.449s ← faster setting, longer audio |
| 1.2 | 5.440s | −19.6% | −16.7% | 5.361s |

`8.1-pro-beta` is monotonic and near-ideal; `mars-instruct` is non-monotonic and wrong-direction
at two points — its 16% noise floor swamps the effect. Single trial per point; 3 trials would
give a curve to fit rather than five dots.

**D5. `mars-pro` does not support Punjabi, and says so with silence.** HTTP 200, **zero bytes**,
no exception, after ~120s. Same model works in Hindi (99KB). A capability probe now runs before
every test. `TTSProvider.validate()` must reject empty or implausibly short audio — in production
this failure looks like success.

**D6. Camb pads ~0.5–0.7s of silence onto every render; Gemini does not.** On a 0.7s line the
pad is longer than the speech. Trim before measuring (or every rate number is wrong) and before
placement (or every line lands late and the dub drifts).

**D7. Models differ 33% in speaking rate.** 34 aksharas → 8.15s on `8.1-pro-beta` vs 6.07s on
`mars-instruct` (4.2 vs 5.6 aksharas/sec). A slower model overflows budgets more often before
any fitting starts.

**D8. Camb has no native voices for 10 of the 12 target languages — ESCALATE.**
Library inventory (793 voices): Hindi 15, Marathi 2, en-US 97, and **zero** for Punjabi,
Kannada, Telugu, Tamil, Malayalam, Bengali, Gujarati, en-IN.
A native Punjabi speaker judged the cross-lingual Punjabi renders (Hindi voice) to mispronounce
words; the same model on the same voice pronounces **Hindi correctly**. So the fault is the
voice, not the model — and no model choice fixes it.
Punjabi is Indo-Aryan and close to Hindi; Dravidian languages are further away and should be
expected to fare worse. **Locale support in the API is not the same as having a voice that
sounds native.** The KT doc names Camb primary TTS because it "covers all 12 languages" — on
this evidence Camb may be viable only for Hindi and Marathi.

**D9. `8.1-pro-beta` is flat in both languages; `mars-instruct` is expressive.** Judged by ear.
Flatness persists in Hindi where pronunciation is correct, so it is intrinsic to the model, not
a cross-lingual artifact. Inline expressive tags are untested — that is the open question for it,
and it is the test that decides whether it is secondary or primary.

### Model roles (provisional)

`mars-8.1-pro-beta` is not out. It is the only model tested with a working `speaking_rate`
lever and the tightest timing consistency measured so far (CV 1.1%, Punjabi), and it is the
only one offering CMU phoneme overrides and non-verbal control. Candidate roles:

| Role | Model | Why |
|---|---|---|
| Primary read, expressive lines | `mars-instruct` (Hindi), TBD elsewhere | expressive, accurate pronunciation |
| **Fit fallback** | `mars-8.1-pro-beta` | when a line will not fit on the expressive model, re-render on the consistent one with the rate lever instead of bouncing it back to the operator |
| Flat-by-nature lines | `mars-8.1-pro-beta` | legal/disclaimer and price lines, where neutral delivery is correct |
| Pronunciation-hostile terms | `mars-8.1-pro-beta` | CMU phoneme overrides for brand names the lexicon cannot fix otherwise |
| Eval baseline | `mars-8.1-pro-beta` | stable reference when measuring other models |

Per-line model selection costs nothing architecturally — the fit ladder already renders,
measures and loops, so "retry on the consistent model" is one more rung. It does mean the
voice must be perceptually close across the two models, which is untested.

**D10. Gemini TTS embeds C2PA/JUMBF provenance metadata (~6KB per render); Camb does not.**
Product decision: shipped ad audio would carry AI-provenance metadata unless stripped. Google
may also apply an inaudible SynthID watermark that re-encoding does not remove — verify before
promising a client either way.

**D11. Latency is erratic.** `mars-pro` ~51s, `8.1-pro-beta` 6–79s for the same workload,
`mars-instruct` ~5s, Gemini ~4s. Irrelevant for a batch product, but it makes timeouts hard to set.

---

## Block G — Stage 5 script adaptation (Punjabi, 12 lines / 5 utterances)

Generators compared through OpenRouter with provider pinning and `data_collection: deny`.
Back-translation glossed by `qwen/qwen3-235b-a22b` — a different family from every generator.
Fit figures use the 7.0 aksharas/sec prior, so they are **relative, not final**, until D4 lands.

| | Claude Sonnet 5 | GPT-5 | Gemini 3.1 Pro |
|---|---|---|---|
| Shippable lines (exact+tight+short) | 7/12 — 58% | **11/12 — 92%** | 9/12 — 75% |
| OVER | 5 | 1 | 3 |
| **Meaning vs original** | **5/5** | 1/5 | 3/5 |
| Meaning vs brief | 5/5 | 3/5 | 4/5 |
| Borrowed words kept | **5/5** | 4/5 | 4/5 |
| Script purity | 5/5 | 5/5 | 5/5 |
| Protected terms | 5/5 | 5/5 | 5/5 |
| Length spread across candidates | 13 ak | 7 ak | 8 ak |
| JSON failures | 0 | 0 | 0 |
| Cost per ad per language | **$0.147** | $0.284 | $0.371 |

Run total: $0.80 for 60 calls, 3 models, 12 lines.

**G1. Fit and fidelity trade off against each other.** GPT-5 fits best and means least: its
endline `Livpure Smart | ਤੁਸੀਂ smart` ("Livpure Smart, you smart") lands inside 0.61s precisely
because it says something else. Claude keeps meaning on all five utterances and overflows five
lines. **Caveat: the first scoring rule picked candidates on timing alone**, which systematically
rewards a model willing to drift. Meaning is now part of the score (see G6), so these fit
numbers overstate GPT-5.

**G2. Short slots are structurally unfittable; long ones fit easily.**

| slot | delta (Claude) |
|---|---|
| 6.01s | −2% |
| 3.44s | **+0.7%** |
| 3.30s | +1% |
| 0.74s | +2% |
| 0.46s (`ਐਕਸ਼ਨ!`) | **+63%** |
| 0.45s (`ਇਹ ਕੀ ਆ?`) | +69% |

At ~7 aksharas/sec a 0.46s slot holds two aksharas, and one more akshara is +50%. `ਐਕਸ਼ਨ!` is
four aksharas ≈ 0.75s: **no Punjabi rendering of "Action!" fits 0.46s**, and the rate lever
buys only ~10%. This is not a copywriting failure and a human operator cannot fix it either.
The fix is gap-borrowing — that line overflows by 0.29s and the ad carries 9.5s of pause
(A8). Short slots are the strongest argument for elasticity in the timing contract.

**G3. Borrowed words are lost during intent extraction, before any target copy exists.**
The `literal` each model wrote for "And | Action!":

- Claude: *"And... Action!"* → kept `ਐਕਸ਼ਨ`
- GPT-5: *"Now, start filming."* → produced "Go! Shoot!"
- Gemini: *"Let us begin the scene."* → produced "Ready? Start!"

The failure is visible one step upstream of where it shows. A literal-field gate now checks
allowlisted terms survive into the brief, with one repair attempt — one string comparison
instead of five wasted candidates.

**G4. Grouping works.** 12 segments → 5 utterances. "And | Action!" written together yields
`ਤੇ... | ਐਕਸ਼ਨ!`, which is unreachable when the slots are adapted independently.

**G5. Checking the gloss against the ORIGINAL catches what the brief check cannot.**
Claude 5/5 vs original; GPT-5 1/5. Both models passed their own brief far more often than they
passed the original, which is the signature of a wrong brief being faithfully executed.
One false positive observed (an ellipsis judged to add "a dramatic pause"), so the flag stays
advisory, as the spec says.

**G6. Scoring now weighs fit and meaning together.** Two stages: shortlist on fit, then gloss
and judge each shortlisted candidate and re-rank. Weights: overflow ×2.0 on fit cost, drift
from original 0.80, drift from brief 0.35, dropped borrowed word 0.25 each. Overflow outranks
drift because a line that cannot fit cannot ship. Costs roughly 2× the calls per utterance.

**G7. Model choice should not be made on price.** $0.15–0.37 per ad per language, i.e. $1.2–3.0
for eight languages, against ~$4 *per language* for lip-sync. The most faithful model here is
also the cheapest.

**Provisional pick: Claude Sonnet 5 as the generator** — fidelity and borrowed-word handling
are hard to engineer around, while the fit gap is exactly what gap-borrowing addresses.
Revisit once D4 gives a measured rate and the meaning-aware score has been re-run.

---

## Corrections to earlier conclusions

Superseded — do not act on these if you heard them earlier:

1. **"A forced aligner is the architecture, not a fallback."** Wrong. The Line owns a time slot
   and a *segment* is a Line; the budget comes from segment boundaries. The aligner is a
   precision upgrade, not critical path. Long multi-sentence segments are split with VAD on the
   voice stem.
2. **"Camb is missing Odia and Assamese."** Partly wrong. The *transcription/translation* enum
   lacks both. The *TTS* locale list includes `as-in` (Assamese) but not `or-in`. So: Odia has
   no Camb path at all; Assamese can be spoken but not transcribed or translated by Camb.
3. **"`mars-8.1-pro-beta` is the *primary* Punjabi model."** Narrowed, not withdrawn. It was
   chosen on timing control alone, before a native speaker found it flat and (cross-lingually)
   mispronouncing. It remains the strongest model tested on the two mechanical axes and is
   **retained as a secondary model** — see "Model roles" below.
4. **"`mars-8.1-pro-beta` is 5× more consistent."** True in Punjabi only. In Hindi the two
   models are level. See D3.
5. **Gemini durations were overstated by ~0.13s per render** by a double-wrapping bug (below).
   The 17% variance conclusion is unchanged, since the error was constant.

## Measurement bugs found and fixed

- **WAV duration read from the header's frame count** — Camb streams audio, so the header carries
  a placeholder; one probe reported **44,739 seconds** for a 132KB file. Now counts decoded samples.
- **Gemini's `inlineData` is a complete WAV, not raw PCM** — it was being wrapped in a second
  header, so the inner header and Google's trailing C2PA box played as audio (0.125s of hiss at
  the end of every render). Now written as-is.
- **Script purity flagged the danda `।` (U+0964) as foreign** in every Punjabi line. It sits in
  the Devanagari block but is shared punctuation across Gurmukhi, Bengali and Odia. Exempted.
- **The allowlist check searched for Latin strings inside target-script text**, so `ਐਕਸ਼ਨ`
  (Action, transliterated — i.e. correct) was reported as "translated away". Now checked against
  the English gloss, where a surviving borrowing glosses back as itself.
- **The word-timestamp walker counted one-word segments as words**, hiding the fact that Camb
  returns none. Now requires an explicit word-level key.

Any number quoted before these fixes should be re-derived from the saved audio, not trusted.

---

## Open

| | |
|---|---|
| Sarvam probe for Punjabi (native Indic voices) | **priority — decides whether Punjabi ships** |
| Expressive tags on `8.1-pro-beta` (Hindi) | decides whether it is primary or secondary, and whether Camb serves Hindi |
| Voice match across the two Camb models | required before per-line model switching is usable |
| Round-trip ASR (CER) to quantify pronunciation errors | also builds the Stage 6 check |
| D4 calibration — real aksharas/sec per voice | every fit number is provisional until this lands |
| Re-run Block G with meaning-aware scoring | the published fit table predates it |
| Gap-borrowing in the timing contract | the only fix for sub-second slots (G2) |
| Tagline as a locked line | models invent regional versions of "The Smart Move" |
| F2 hand-timing | unlocks A1/A2/A3/A5 boundary-error numbers |
| Google Cloud STT | blocked on billing |
| Azure / AI4Bharat for Odia | no Camb path exists |
