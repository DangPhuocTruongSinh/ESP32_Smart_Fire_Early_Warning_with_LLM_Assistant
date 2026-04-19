"""
Gmail SMTP alert sender cho hệ thống Early Fire Alarm.

Gửi email cảnh báo cháy qua Gmail SMTPS (smtp.gmail.com:465) dùng App Password.
Quy trình Gmail yêu cầu:
    1. Bật xác thực 2 bước cho tài khoản Gmail.
    2. Vào Google Account → Security → App passwords → tạo password 16 ký tự.
    3. Lưu 3 biến môi trường dưới đây vào file .env của project:
        GMAIL_SENDER         = địa chỉ Gmail gửi đi
        GMAIL_APP_PASSWORD   = App Password vừa tạo (có thể có dấu cách)
        ALERT_RECIPIENTS     = danh sách người nhận, ngăn cách bằng dấu phẩy

Nếu thiếu biến env, module sẽ log warning và return False mà KHÔNG raise —
để luồng phát hiện cháy phía trên không bị chết vì lỗi cấu hình email.
"""

import os
import smtplib
import socket
import ssl
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .log import logger

load_dotenv()

# SMTP endpoint cố định theo Google
_SMTP_HOST            = "smtp.gmail.com"
_SMTP_PORT            = 465
_SMTP_TIMEOUT_SECONDS = 10

# Giới hạn retry để không khoá MQTT thread quá lâu khi mạng hỏng
_MAX_RETRIES          = 2
_RETRY_BACKOFF_BASE_S = 2.0

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _load_config() -> Optional[Dict[str, Any]]:
    """
    Đọc config Gmail từ biến môi trường và validate tối thiểu.

    Log rõ các biến đang thiếu để người triển khai biết sửa ở đâu,
    nhưng không raise để caller tự quyết định fallback.

    Returns:
        Dict {sender, password, recipients[]} nếu đủ cả 3; None nếu thiếu.
    """
    sender     = os.getenv("GMAIL_SENDER", "").strip()
    password   = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    recipients = os.getenv("ALERT_RECIPIENTS", "").strip()

    missing = [
        name for name, value in (
            ("GMAIL_SENDER", sender),
            ("GMAIL_APP_PASSWORD", password),
            ("ALERT_RECIPIENTS", recipients),
        ) if not value
    ]
    if missing:
        logger.warning(
            "Gmail alert disabled — missing env var(s): %s",
            ", ".join(missing),
        )
        return None

    return {
        "sender":     sender,
        "password":   password,
        "recipients": [r.strip() for r in recipients.split(",") if r.strip()],
    }


def _build_message(event: Dict[str, Any],
                   sender: str,
                   recipients: List[str]) -> MIMEMultipart:
    """
    Dựng MIMEMultipart (plain + HTML alternative) cho email cảnh báo.

    Tách riêng khỏi send_fire_alert để test compose message mà không cần SMTP.

    Args:
        event:      Dict alert (device, confidence, mode, sensor_snapshot, detected_at).
        sender:     Địa chỉ From.
        recipients: Danh sách địa chỉ To.

    Returns:
        Đối tượng MIMEMultipart sẵn sàng cho sendmail.
    """
    device     = event.get("device", "unknown")
    confidence = float(event.get("confidence", 0.0))
    mode       = event.get("mode", "unknown")
    snapshot   = event.get("sensor_snapshot") or {}

    detected = event.get("detected_at") or datetime.now(_TZ)
    if isinstance(detected, (int, float)):
        detected = datetime.fromtimestamp(detected, tz=_TZ)
    time_str = detected.strftime("%H:%M:%S %d/%m/%Y")

    subject = f"[CẢNH BÁO CHÁY] {device} — {time_str}"

    # Plain text fallback cho email client không render HTML
    plain_lines = [
        "HỆ THỐNG EARLY FIRE ALARM — PHÁT HIỆN NGUY CƠ CHÁY",
        "",
        f"Thiết bị   : {device}",
        f"Thời điểm  : {time_str}",
        f"Chế độ     : {mode}",
        f"Độ tin cậy : {confidence * 100:.1f}%",
        "",
        "Chỉ số cảm biến tại thời điểm cảnh báo:",
    ]
    if snapshot:
        for label, value in snapshot.items():
            plain_lines.append(f"  - {label}: {value}")
    else:
        plain_lines.append("  (không có dữ liệu snapshot)")
    plain_lines += [
        "",
        "Vui lòng kiểm tra khu vực ngay lập tức.",
        "Email này được gửi tự động, không cần trả lời.",
    ]
    plain_body = "\n".join(plain_lines)

    # Bảng HTML cho phần snapshot (trống → hiển thị placeholder)
    if snapshot:
        rows = "".join(
            f"<tr>"
            f"<td style='padding:6px 14px;color:#555;border-bottom:1px solid #eee;'>{label}</td>"
            f"<td style='padding:6px 14px;font-weight:bold;border-bottom:1px solid #eee;'>{value}</td>"
            f"</tr>"
            for label, value in snapshot.items()
        )
    else:
        rows = (
            "<tr><td colspan='2' style='padding:10px;color:#999;'>"
            "(không có dữ liệu snapshot)</td></tr>"
        )

    html_body = f"""\
<!DOCTYPE html>
<html lang="vi">
  <body style="font-family:Arial,Helvetica,sans-serif;color:#222;margin:0;padding:16px;">
    <div style="max-width:580px;margin:auto;border:1px solid #d43b3b;border-radius:8px;overflow:hidden;">
      <div style="background:#d43b3b;color:#fff;padding:14px 18px;">
        <h2 style="margin:0;font-size:20px;">CẢNH BÁO CHÁY</h2>
        <p style="margin:4px 0 0;font-size:13px;opacity:0.9;">
          Early Fire Alarm System
        </p>
      </div>
      <div style="padding:16px 18px;">
        <p style="margin:6px 0;"><strong>Thời điểm:</strong> {time_str}</p>
        <p style="margin:6px 0;"><strong>Thiết bị:</strong> {device}</p>
        <p style="margin:6px 0;"><strong>Chế độ phát hiện:</strong> {mode}</p>
        <p style="margin:6px 0;"><strong>Độ tin cậy:</strong>
          <span style="color:#d43b3b;font-weight:bold;">
            {confidence * 100:.1f}%
          </span>
        </p>

        <h3 style="margin:18px 0 8px;font-size:15px;">Chỉ số cảm biến</h3>
        <table style="border-collapse:collapse;font-size:14px;width:100%;">
          {rows}
        </table>

        <p style="margin-top:22px;color:#555;font-size:13px;">
          Vui lòng kiểm tra khu vực ngay lập tức.<br/>
          Email này được gửi tự động, không cần trả lời.
        </p>
      </div>
    </div>
  </body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,  "html",  "utf-8"))
    return msg


def send_fire_alert(event: Dict[str, Any]) -> bool:
    """
    Gửi email cảnh báo cháy qua Gmail SMTP (đồng bộ, có retry, KHÔNG raise).

    Args:
        event: Dict chứa các trường:
            - device (str):              ID thiết bị phát hiện cháy.
            - confidence (float 0-1):    Độ tin cậy từ model.
            - mode (str):                Ví dụ "cnn1d_tflite", "rule_based".
            - sensor_snapshot (dict):    Chỉ số cảm biến (tuỳ chọn).
            - detected_at (datetime|float): Thời điểm phát hiện (mặc định now).

    Returns:
        True nếu sendmail thành công ở lần thử nào đó; False nếu đã thử hết
        retry mà vẫn fail — lỗi đã được log chi tiết.
    """
    cfg = _load_config()
    if cfg is None:
        return False

    msg = _build_message(event, cfg["sender"], cfg["recipients"])
    context = ssl.create_default_context()

    last_err: Optional[Exception] = None
    total_attempts = _MAX_RETRIES + 1

    for attempt in range(1, total_attempts + 1):
        try:
            with smtplib.SMTP_SSL(
                _SMTP_HOST, _SMTP_PORT,
                timeout=_SMTP_TIMEOUT_SECONDS,
                context=context,
            ) as smtp:
                smtp.login(cfg["sender"], cfg["password"])
                smtp.sendmail(cfg["sender"], cfg["recipients"], msg.as_string())

            logger.info(
                "Fire alert email sent to %d recipient(s), device=%s confidence=%.1f%%",
                len(cfg["recipients"]),
                event.get("device"),
                float(event.get("confidence", 0.0)) * 100,
            )
            return True

        except (socket.timeout, smtplib.SMTPException, OSError) as e:
            last_err = e
            logger.warning(
                "SMTP send attempt %d/%d failed: %s",
                attempt, total_attempts, e,
            )
            # Exponential backoff: 2s, 4s (không sleep sau lần cuối)
            if attempt < total_attempts:
                time.sleep(_RETRY_BACKOFF_BASE_S * attempt)

    logger.error(
        "Gave up sending fire alert email after %d attempts — last error: %s",
        total_attempts, last_err,
    )
    return False


def send_fire_alert_async(event: Dict[str, Any]) -> threading.Thread:
    """
    Gửi email trong thread daemon riêng để không block caller.

    Dùng cho MQTT callback — nếu chặn ở đó, vòng lặp network của paho
    sẽ delay và có thể mất ACK cho các message tiếp theo.

    Args:
        event: Xem send_fire_alert.

    Returns:
        Thread daemon đã start. Caller thường không cần join.
    """
    t = threading.Thread(
        target=send_fire_alert,
        args=(event,),
        name="gmail-alert-sender",
        daemon=True,
    )
    t.start()
    return t
