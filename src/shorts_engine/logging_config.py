import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from shorts_engine.db.base import get_data_dir

_HANDLER_MARKER = "shorts_engine_rotating_handler"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def configure_logging(data_dir: Path | None = None) -> Path:
    log_dir = (data_dir or get_data_dir()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "shorts-engine.log"

    root_logger = logging.getLogger()
    if not any(getattr(handler, _HANDLER_MARKER, False) for handler in root_logger.handlers):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        setattr(handler, _HANDLER_MARKER, True)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root_logger.addHandler(handler)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid LOG_LEVEL: {level_name}")
    root_logger.setLevel(level)
    return log_path
