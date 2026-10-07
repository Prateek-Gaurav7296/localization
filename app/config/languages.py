"""Central registry of supported languages. The UI and pipeline read languages only from here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # ISO 639-1, the identifier passed through the pipeline
    name: str  # English display name
    native_name: str
    locale: str  # BCP 47 tag, for STT/TTS vendors that want a region

    @property
    def label(self) -> str:
        return self.name if self.name == self.native_name else f"{self.name} ({self.native_name})"


LANGUAGES: tuple[Language, ...] = (
    Language("hi", "Hindi", "हिन्दी", "hi-IN"),
    Language("en", "English", "English", "en-IN"),
    Language("te", "Telugu", "తెలుగు", "te-IN"),
    Language("ml", "Malayalam", "മലയാളം", "ml-IN"),
    Language("kn", "Kannada", "ಕನ್ನಡ", "kn-IN"),
    Language("ta", "Tamil", "தமிழ்", "ta-IN"),
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ", "pa-IN"),
    Language("bn", "Bengali", "বাংলা", "bn-IN"),
)

_BY_CODE = {language.code: language for language in LANGUAGES}


def get_language(code: str) -> Language:
    try:
        return _BY_CODE[code.strip().lower()]
    except KeyError:
        supported = ", ".join(_BY_CODE)
        raise ValueError(f"Unsupported language {code!r}. Supported: {supported}") from None
