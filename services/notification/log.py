"""
Logger riêng cho notification service.

Ghi log ra console + file logs/notification.log, rotate 5 MB x 3 backup.
Tách khỏi llm_service để dễ trace các sự cố SMTP / MQTT alert
mà không bị nhiễu bởi log của agent chatbot.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR    = os.path.join(os.path.dirname(__file__), "logs")
_LOG_FILE   = os.path.join(_LOG_DIR, "notification.log")
_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(message)s"
_DATE_FMT   = "%Y-%m-%d %H:%M:%S"

os.makedirs(_LOG_DIR, exist_ok=True)

logger = logging.getLogger("notification")
logger.setLevel(logging.DEBUG)

# Guard để tránh gắn handler trùng khi module được import nhiều lần
# (ví dụ reload trong FastAPI dev).
if not logger.handlers:
    _console = logging.StreamHandler()
    _console.setLevel(logging.INFO)
    _console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT))

    _file = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file.setLevel(logging.DEBUG)
    _file.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT))

    logger.addHandler(_console)
    logger.addHandler(_file)
