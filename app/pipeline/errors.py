"""Pipeline-level errors. Every failure surfaces as a PipelineError naming the stage that failed."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.pipeline.models import LocalizationResult, Stage


class PipelineError(Exception):
    """A stage failed. ``str(error)`` reads like "STT failed: <reason>".

    ``result`` holds whatever the run produced before the failure (run id, input artifacts, ...).
    The original exception is chained as ``__cause__``.
    """

    def __init__(self, stage: Stage, message: str, result: LocalizationResult | None = None):
        super().__init__(f"{stage.failure_label}: {message}")
        self.stage = stage
        self.message = message
        self.result = result


class EngineError(Exception):
    """Raised by intermediate engines (STT/TTT/TTS). The message is shown to the operator."""


class EngineNotAvailableError(EngineError):
    """The configured engine is not integrated or not configured yet."""
