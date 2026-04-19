"""
Logging module cho LLM service.

Cung cấp logger dùng chung với 2 handler:
  - Console (StreamHandler): in ra stdout để xem trực tiếp khi chạy.
  - File (RotatingFileHandler): ghi vào logs/llm_service.log, tự xoay vòng
    khi đạt 5 MB, giữ tối đa 3 file backup.

Cấu trúc log:
  YYYY-MM-DD HH:MM:SS [LEVEL  ] message
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "llm_service.log")
_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

os.makedirs(_LOG_DIR, exist_ok=True)

logger = logging.getLogger("llm_service")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    # ── Console handler ──────────────────────────────────
    _console = logging.StreamHandler()
    _console.setLevel(logging.DEBUG)
    _console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    # ── Rotating file handler ────────────────────────────
    _file = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    _file.setLevel(logging.DEBUG)
    _file.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    logger.addHandler(_console)
    logger.addHandler(_file)


def log_user_message(thread_id: str, question: str) -> None:
    """
    Ghi log câu hỏi của người dùng.

    Args:
        thread_id: ID cuộc hội thoại.
        question:  Nội dung câu hỏi (chưa bao gồm time-context inject).
    """
    logger.info("[thread=%s] USER: %s", thread_id, question)


def log_tool_calls(thread_id: str, messages: list) -> None:
    """
    Duyệt danh sách messages sau khi agent chạy xong và ghi log
    tất cả tool call + kết quả trả về.

    LangGraph đặt tool calls trong AIMessage.tool_calls và kết quả
    trong ToolMessage.content theo thứ tự xen kẽ trong messages list.

    Args:
        thread_id: ID cuộc hội thoại.
        messages:  response["messages"] từ agent.ainvoke.
    """
    for msg in messages:
        msg_type = type(msg).__name__

        # AIMessage có thể chứa tool_calls nếu LLM quyết định gọi tool
        if msg_type == "AIMessage" and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                args_str = ", ".join(f"{k}={v!r}" for k, v in tc.get("args", {}).items())
                logger.debug(
                    "[thread=%s] TOOL CALL: %s(%s)",
                    thread_id, tc.get("name", "?"), args_str,
                )

        # ToolMessage chứa kết quả trả về từ tool
        elif msg_type == "ToolMessage":
            content = getattr(msg, "content", "")
            # Cắt bớt nếu quá dài để log dễ đọc
            preview = content if len(content) <= 200 else content[:200] + "…"
            logger.debug(
                "[thread=%s] TOOL RESULT [%s]: %s",
                thread_id, getattr(msg, "name", "?"), preview,
            )


def log_assistant_response(thread_id: str, response: str) -> None:
    """
    Ghi log phản hồi cuối cùng của LLM gửi về người dùng.

    Args:
        thread_id: ID cuộc hội thoại.
        response:  Nội dung phản hồi.
    """
    logger.info("[thread=%s] ASSISTANT: %s", thread_id, response)


def log_error(thread_id: str, error: Exception) -> None:
    """
    Ghi log lỗi xảy ra trong quá trình xử lý request.

    Args:
        thread_id: ID cuộc hội thoại.
        error:     Exception cần log.
    """
    logger.error("[thread=%s] ERROR: %s", thread_id, error, exc_info=True)
