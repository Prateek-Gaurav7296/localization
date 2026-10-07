"""Adapter for Ishnit's STT -> TTT -> TTS implementation (``Ishnit/``, not yet delivered).

This is the only file that should import from ``Ishnit/``. When the implementation lands:

1. Import Ishnit's entry points here (the code in ``Ishnit/`` itself stays untouched).
2. Implement the three methods by converting ``IntermediateRequest`` into whatever Ishnit's code
   expects and its results into ``Transcript`` / ``Translation`` / ``SynthesizedAudio``.
3. Wrap vendor/library failures in ``EngineError`` with a readable message.
4. Set ``LOCALIZATION_ENGINE=ishnit``.

If Ishnit's code runs STT/TTT/TTS as one call, run it in ``transcribe`` and keep the later
results on the instance for ``translate``/``synthesize`` to return.
"""

from __future__ import annotations

from app.pipeline.errors import EngineNotAvailableError
from app.pipeline.intermediate.contract import (
    IntermediateRequest,
    LocalizationEngine,
    SynthesizedAudio,
    Transcript,
    Translation,
)

_NOT_INTEGRATED = (
    "Ishnit's STT/TTT/TTS implementation is not integrated yet. "
    "Set LOCALIZATION_ENGINE=mock to exercise the pipeline in development."
)


class IshnitEngine(LocalizationEngine):
    name = "ishnit"

    def transcribe(self, request: IntermediateRequest) -> Transcript:
        raise EngineNotAvailableError(_NOT_INTEGRATED)

    def translate(self, transcript: Transcript, request: IntermediateRequest) -> Translation:
        raise EngineNotAvailableError(_NOT_INTEGRATED)

    def synthesize(self, translation: Translation, request: IntermediateRequest) -> SynthesizedAudio:
        raise EngineNotAvailableError(_NOT_INTEGRATED)
