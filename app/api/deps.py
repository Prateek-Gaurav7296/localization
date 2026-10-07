"""FastAPI dependencies shared by the API routes."""

from __future__ import annotations

from app.config import InputSettings, get_settings


def get_input_settings() -> InputSettings:
    return get_settings().input
