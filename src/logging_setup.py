"""Console and file logging. The file keeps a more detailed level than the terminal."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.config import resolve_path

_MARKER = "_value_signals"
_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


def configure_logging(
    *,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    log_file: str | Path | None = "logs/value_signals.log",
    console_stream=None,
) -> None:
    """Attach one console handler and one file handler to the root logger.

    Calling this again replaces the handlers installed by a previous call,
    so a Streamlit rerun does not stack duplicate files.
    """
    console = _parse_level(console_level, logging.INFO)
    detailed = _parse_level(file_level, logging.DEBUG)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARKER, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(min(console, detailed))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler(console_stream if console_stream is not None else sys.stderr)
    stream.setLevel(console)
    stream.setFormatter(formatter)
    setattr(stream, _MARKER, True)
    root.addHandler(stream)

    if not log_file:
        return
    path = resolve_path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        root.warning("Could not open log file %s: %s", path, exc)
        return
    file_handler.setLevel(detailed)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _MARKER, True)
    root.addHandler(file_handler)


def _parse_level(name: str, default: int) -> int:
    return _LEVELS.get(str(name).upper(), default)
