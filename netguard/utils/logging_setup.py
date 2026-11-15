"""Logging configuration shared by the CLI and scripts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: Union[int, str] = "INFO", logfile: Optional[Union[str, Path]] = None) -> None:
    """Configure the root logger (console, plus an optional log file)."""
    handlers = [logging.StreamHandler()]
    if logfile is not None:
        path = Path(logfile)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers, force=True)
