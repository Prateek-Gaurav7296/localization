"""Mock STT/TTT/TTS engine (development only).

transcribe/translate return clearly-labelled placeholder text; synthesize returns the source's
first audio track unchanged, so the output is NOT translated.
"""

from __future__ import annotations

import logging

from app.config import InputSettings
from app.pipeline.input.ffmpeg_service import run_ffmpeg
from app.pipeline.intermediate.contract import (
    IntermediateRequest,
    LocalizationEngine,
    SynthesizedAudio,
    Transcript,
    Translation,
)

logger = logging.getLogger(__name__)


class MockLocalizationEngine(LocalizationEngine):
    name = "mock"

    def __init__(self, settings: InputSettings) -> None:
        self._settings = settings

    def transcribe(self, request: IntermediateRequest) -> Transcript:
        language = request.source_language.code if request.source_language else "und"
        return Transcript(language=language, text="[MOCK TRANSCRIPT] No speech recognition was performed.")

    def translate(self, transcript: Transcript, request: IntermediateRequest) -> Translation:
        return Translation(
            source_language=transcript.language,
            target_language=request.target_language.code,
            text=f"[MOCK TRANSLATION] No translation to {request.target_language.name} was performed.",
        )

    def synthesize(self, translation: Translation, request: IntermediateRequest) -> SynthesizedAudio:
        output = request.work_dir / "mock_tts_audio.m4a"
        run_ffmpeg(
            self._settings.ffmpeg_binary,
            ["-i", str(request.source_audio), "-map", "0:a:0", "-c", "copy", "-f", "ipod", str(output)],
            self._settings.ffmpeg_timeout_seconds,
            logging.LoggerAdapter(logger, {}),
        )
        return SynthesizedAudio(path=output, duration_seconds=request.audio_duration_seconds)
