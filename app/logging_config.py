"""Single logging setup shared by the API and the Streamlit UI."""

from __future__ import annotations

import logging

_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if not _configured:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger("app").addHandler(handler)
        logging.getLogger("app").propagate = False
        # httpx logs every request URL at INFO, which includes Sync.so's signed output URL.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        _configured = True
    logging.getLogger("app").setLevel(level)
