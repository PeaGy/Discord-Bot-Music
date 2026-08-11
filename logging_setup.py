"""Cấu hình logging dùng chung cho bot và các extension."""

import logging
import os


LOG_FORMAT = "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s"


def configure_logging() -> None:
    """Thiết lập console log một lần, với mức log lấy từ biến LOG_LEVEL."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format=LOG_FORMAT,
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root_logger.setLevel(level)

    logging.getLogger("discord").setLevel(max(level, logging.INFO))
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
