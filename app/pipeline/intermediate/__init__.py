"""Intermediate stage (STT -> TTT -> TTS): the engine contract and engine selection."""

from __future__ import annotations

from app.config import Settings
from app.pipeline.intermediate.contract import (
    IntermediateRequest,
    IntermediateResult,
    LocalizationEngine,
    Segment,
    SynthesizedAudio,
    Transcript,
    Translation,
)


def get_engine(settings: Settings) -> LocalizationEngine:
    """Build the engine selected by ``LOCALIZATION_ENGINE``."""
    if settings.pipeline.engine == "ishnit":
        from app.pipeline.intermediate.ishnit_engine import IshnitEngine

        return IshnitEngine()
    if settings.pipeline.engine == "mock":
        from app.dev.mock_engine import MockLocalizationEngine  # development only

        return MockLocalizationEngine(settings.input)
    raise ValueError(f"Unknown LOCALIZATION_ENGINE {settings.pipeline.engine!r}")


__all__ = [
    "IntermediateRequest",
    "IntermediateResult",
    "LocalizationEngine",
    "Segment",
    "SynthesizedAudio",
    "Transcript",
    "Translation",
    "get_engine",
]
