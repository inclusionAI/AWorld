from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

GATEWAY_LOGGER_NAME = "aworld.gateway"
_GATEWAY_HANDLER_MARKER = "_aworld_gateway_file_handler"


def resolve_gateway_log_path(log_path: Path | str | None = None) -> Path:
    if log_path is not None:
        return Path(log_path).expanduser().resolve()

    configured_path = str(os.getenv("AWORLD_GATEWAY_LOG_PATH") or "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    configured_dir = str(os.getenv("AWORLD_LOG_PATH") or "").strip()
    if configured_dir:
        return (Path(configured_dir).expanduser() / "gateway.log").resolve()

    return (Path.cwd() / "logs" / "gateway.log").resolve()


def configure_gateway_logging(*, log_path: Path | str | None = None) -> Path:
    resolved_path = resolve_gateway_log_path(log_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    gateway_root_logger = logging.getLogger(GATEWAY_LOGGER_NAME)
    gateway_root_logger.setLevel(logging.INFO)
    gateway_root_logger.propagate = True

    for handler in list(gateway_root_logger.handlers):
        if not getattr(handler, _GATEWAY_HANDLER_MARKER, False):
            continue
        if Path(getattr(handler, "baseFilename", "")).resolve() == resolved_path:
            return resolved_path
        gateway_root_logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        resolved_path,
        maxBytes=32 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    setattr(handler, _GATEWAY_HANDLER_MARKER, True)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    gateway_root_logger.addHandler(handler)
    return resolved_path


def get_gateway_logger(component: str | None = None) -> logging.Logger:
    configure_gateway_logging()
    if not component:
        return logging.getLogger(GATEWAY_LOGGER_NAME)
    normalized_component = str(component).strip().replace(" ", "_")
    return logging.getLogger(f"{GATEWAY_LOGGER_NAME}.{normalized_component}")
