"""File and console logs use different levels."""

import io
import logging

from src.logging_setup import configure_logging


def test_the_log_file_keeps_debug_lines_the_console_omits(tmp_path):
    console = io.StringIO()
    path = tmp_path / "scan.log"
    configure_logging(
        console_level="INFO",
        file_level="DEBUG",
        log_file=path,
        console_stream=console,
    )
    try:
        logger = logging.getLogger("src.pipeline")
        logger.debug("hidden detail")
        logger.info("visible line")
        logger.warning("careful")
        text = path.read_text(encoding="utf-8")
        assert "hidden detail" in text
        assert "visible line" in text
        assert "DEBUG" in text
        shown = console.getvalue()
        assert "hidden detail" not in shown
        assert "visible line" in shown
        assert "careful" in shown
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_value_signals", False):
                root.removeHandler(handler)
                handler.close()
