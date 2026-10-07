"""The contract between the pipeline and any STT -> TTT -> TTS implementation.

The pipeline hands an engine an ``IntermediateRequest`` (the input stage's artifacts plus the
languages) and calls the three steps in order. Each step returns a plain data object, so engines
are free to use any vendor or model internally.

To plug in an implementation: subclass ``LocalizationEngine``, implement the three methods, and
register it in ``app/pipeline/intermediate/__init__.py``. Nothing else in the app needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.config.languages import Language


@dataclass(frozen=True)
class Segment:
    """A timed piece of speech. Times are seconds from the start of the source audio."""

    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass(frozen=True)
class IntermediateRequest:
    run_id: str
    work_dir: Path  # empty directory reserved for this engine's files (run_dir/intermediate)
    source_audio: Path  # audio.m4a from the input stage; may hold several tracks (dialogue + music)
    silent_video: Path  # video.mp4 from the input stage, for engines that need visual timing
    source_language: Language | None  # None: the engine should detect the language
    target_language: Language
    audio_duration_seconds: float | None


@dataclass(frozen=True)
class Transcript:
    language: str  # ISO 639-1 code actually transcribed (detected if source_language was None)
    text: str
    segments: tuple[Segment, ...] = ()


@dataclass(frozen=True)
class Translation:
    source_language: str
    target_language: str
    text: str
    segments: tuple[Segment, ...] = ()  # same timing as the transcript where the engine can keep it


@dataclass(frozen=True)
class SynthesizedAudio:
    """The localized audio track the output stage will lip-sync the video to.

    This is the complete final soundtrack: if the ad's music/background should be kept, the
    engine is responsible for mixing it back in. Must be a Sync.so-compatible format
    (.wav .mp3 .m4a .aac .ogg .flac ...) and under 20MB.
    """

    path: Path
    duration_seconds: float | None = None


@dataclass(frozen=True)
class IntermediateResult:
    transcript: Transcript
    translation: Translation
    audio: SynthesizedAudio


class LocalizationEngine(ABC):
    """STT -> TTT -> TTS. Raise ``app.pipeline.errors.EngineError`` with a readable message on failure."""

    name: str = "engine"

    @abstractmethod
    def transcribe(self, request: IntermediateRequest) -> Transcript:
        """Speech-to-text on ``request.source_audio``."""

    @abstractmethod
    def translate(self, transcript: Transcript, request: IntermediateRequest) -> Translation:
        """Translate/transform the transcript into ``request.target_language``."""

    @abstractmethod
    def synthesize(self, translation: Translation, request: IntermediateRequest) -> SynthesizedAudio:
        """Text-to-speech: write the localized audio into ``request.work_dir``."""

    def process(self, request: IntermediateRequest) -> IntermediateResult:
        """Run all three steps. The orchestrator calls the steps individually to report progress."""
        transcript = self.transcribe(request)
        translation = self.translate(transcript, request)
        return IntermediateResult(transcript, translation, self.synthesize(translation, request))
