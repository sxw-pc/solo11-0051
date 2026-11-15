"""Minimal logging setup; the library never configures logging on import."""

from __future__ import annotations

import logging
import sys

def get_logger(name: str = "samplelaw") -> logging.Logger:
    return logging.getLogger(name)

def configure_logging(verbose: bool = False) -> logging.Logger:
    logger = get_logger()
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.propagate = False
    return logger
