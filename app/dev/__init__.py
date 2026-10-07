"""DEVELOPMENT-ONLY stand-ins. Nothing here translates speech or lip-syncs video.

They exist so the pipeline and UI can be exercised end to end before Ishnit's STT/TTT/TTS is
integrated and without a Sync.so API key. They are only loaded when explicitly selected with
``LOCALIZATION_ENGINE=mock`` or ``LIPSYNC_PROVIDER=mock``, and the UI flags them prominently.
"""
