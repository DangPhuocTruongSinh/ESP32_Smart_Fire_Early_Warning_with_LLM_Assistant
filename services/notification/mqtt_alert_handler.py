"""
MQTT alert handler — nhận topic `iot/+/alert` từ ESP32 và dispatch email.

Luồng xử lý:
    1. ESP32 publish {"status":"FIRE_DETECTED","confidence":...,"mode":...}
       lên `iot/<device>/alert` khi CNN / rule phát hiện cháy.
    2. Callback parse payload, áp debounce theo ALERT_COOLDOWN_SECONDS.
    3. Nếu ngoài cooldown → spawn thread gửi Gmail (có đính kèm snapshot
       cảm biến mới nhất lấy từ env_snapshot_getter).
    4. Nếu trong cooldown → chỉ log, KHÔNG gửi, tránh spam hộp thư khi
       ESP32 publish alert liên tục trong lúc đám cháy đang diễn ra.

Chiến lược callback: dùng `mqtt_client.message_callback_add("iot/+/alert", _on_alert)`.
Paho-mqtt ưu tiên callback khớp pattern cụ thể hơn `on_message` mặc định,
nên không xung đột với logic ghi InfluxDB ở mqtt_to_influxdb.py.
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .gmail_alert import send_fire_alert_async
from .log import logger

load_dotenv()

_DEFAULT_COOLDOWN_SECONDS = 300  # 5 phút
_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Type alias cho callable trả về dict snapshot env — cho phép None để skip snapshot
EnvSnapshotGetter = Optional[Callable[[], Dict[str, Any]]]


class _AlertState:
    """
    State dùng chung giữa các lần nhận alert để implement debounce.

    MQTT on_message được gọi từ network thread của paho, nên truy cập
    last_sent_ts phải bảo vệ bằng Lock để tránh race nếu có nhiều alert
    song song (ví dụ nhiều thiết bị ESP32 cùng publish).
    """

    def __init__(self, cooldown_seconds: float) -> None:
        self.cooldown     = cooldown_seconds
        self.last_sent_ts = 0.0
        self.lock         = threading.Lock()

    def try_claim(self, now: float) -> bool:
        """
        Atomically kiểm tra & cập nhật last_sent_ts nếu đã hết cooldown.

        Args:
            now: Unix timestamp hiện tại.

        Returns:
            True nếu caller được phép gửi email (đã set last_sent_ts = now);
            False nếu còn trong cooldown.
        """
        with self.lock:
            if now - self.last_sent_ts >= self.cooldown:
                self.last_sent_ts = now
                return True
            return False

    def seconds_remaining(self, now: float) -> float:
        """Số giây còn lại của cooldown hiện tại (>=0)."""
        with self.lock:
            return max(0.0, self.cooldown - (now - self.last_sent_ts))


def _parse_cooldown_env() -> float:
    """Đọc ALERT_COOLDOWN_SECONDS, fallback default nếu thiếu hoặc sai định dạng."""
    raw = os.getenv("ALERT_COOLDOWN_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_COOLDOWN_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning(
            "ALERT_COOLDOWN_SECONDS='%s' invalid — using default %ds",
            raw, _DEFAULT_COOLDOWN_SECONDS,
        )
        return _DEFAULT_COOLDOWN_SECONDS


def _device_id_from_topic(topic: str) -> str:
    """
    Trích device id từ topic `iot/<device>/alert`. Trả về topic gốc nếu
    format không khớp (phòng trường hợp broker cấu hình khác).
    """
    parts = topic.split("/")
    return parts[1] if len(parts) >= 3 else topic


# Cấu hình hiển thị snapshot trong email: (key trong payload, nhãn VI, đơn vị, số chữ số)
# Đặt ở đây chứ không trong gmail_alert để thay đổi hiển thị không cần đụng SMTP code.
_SNAPSHOT_SCHEMA = (
    ("temperature",   "Nhiệt độ",       "°C",    1),
    ("humidity",      "Độ ẩm",          "%",     1),
    ("gas",           "Gas (MQ2)",      "",      0),
    ("co",            "CO",             "ppm",   1),
    ("voc",           "VOC",            "",      1),
    ("h2",            "H2",             "",      1),
    ("pm05",          "PM0.5",          "µg/m³", 1),
    ("pm10",          "PM1.0",          "µg/m³", 1),
    ("pm_total",      "PM tổng",        "µg/m³", 1),
    ("uv",            "UV index",       "",      2),
    ("ml_class",      "Kết quả ML",     "",      None),
    ("ml_confidence", "ML confidence",  "",      None),
)


def _format_snapshot(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """
    Chuyển snapshot thô (key tiếng Anh) thành dict {nhãn VI: giá trị đã format}.

    Args:
        snapshot: Dict cảm biến từ mqtt_to_influxdb.get_latest_env().

    Returns:
        Dict đã format để hiển thị trong email (đơn vị + làm tròn).
        Trả về {} nếu snapshot rỗng.
    """
    if not snapshot:
        return {}

    out: Dict[str, str] = {}
    for key, label, unit, ndigits in _SNAPSHOT_SCHEMA:
        if key not in snapshot:
            continue
        val = snapshot[key]
        if isinstance(val, (int, float)) and ndigits is not None:
            text = f"{val:.{ndigits}f}"
            if unit:
                text = f"{text} {unit}"
        else:
            text = str(val)
            if unit:
                text = f"{text} {unit}"
        out[label] = text
    return out


def register_fire_alert_handler(
    mqtt_client,
    env_snapshot_getter: EnvSnapshotGetter = None,
    topic_filter: str = "iot/+/alert",
) -> None:
    """
    Đăng ký callback xử lý fire alert vào MQTT client đang có.

    Gọi MỘT lần sau khi mqtt_client đã khởi tạo, trước `loop_forever()`.
    Không trùng với on_message mặc định vì dùng message_callback_add —
    paho ưu tiên callback pattern cụ thể hơn handler mặc định.

    Args:
        mqtt_client:         Đối tượng paho.mqtt.client.Client đã khởi tạo.
        env_snapshot_getter: Callable không tham số trả về dict snapshot env
                             mới nhất (để đính kèm vào email). Nếu None → email
                             sẽ không có phần "Chỉ số cảm biến".
        topic_filter:        MQTT topic filter để match alert — mặc định
                             "iot/+/alert" phù hợp với ESP32_01 hiện tại và
                             mở rộng được cho nhiều device sau này.
    """
    state = _AlertState(_parse_cooldown_env())

    def _on_alert(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Invalid alert payload on %s: %s", msg.topic, e)
            return

        # Chỉ xử lý FIRE_DETECTED. Các status khác (ví dụ "CLEAR") để tương lai.
        status = str(payload.get("status", "")).upper()
        if status != "FIRE_DETECTED":
            logger.debug(
                "Ignoring non-fire alert status=%s on %s", status, msg.topic,
            )
            return

        device_id  = _device_id_from_topic(msg.topic)
        confidence = float(payload.get("confidence", 0.0))
        mode       = str(payload.get("mode", "unknown"))

        now = time.time()
        if not state.try_claim(now):
            logger.info(
                "Fire alert from %s suppressed by cooldown "
                "(%.0fs remaining, mode=%s, confidence=%.1f%%)",
                device_id,
                state.seconds_remaining(now),
                mode,
                confidence * 100,
            )
            return

        # Lấy snapshot — bảo vệ khỏi exception trong getter để không mất alert
        snapshot_raw: Dict[str, Any] = {}
        if env_snapshot_getter is not None:
            try:
                snapshot_raw = env_snapshot_getter() or {}
            except Exception as e:
                logger.warning("env_snapshot_getter raised: %s", e)

        event = {
            "device":          device_id,
            "confidence":      confidence,
            "mode":            mode,
            "sensor_snapshot": _format_snapshot(snapshot_raw),
            "detected_at":     datetime.now(_TZ),
        }

        logger.warning(
            "FIRE_DETECTED device=%s confidence=%.1f%% mode=%s → dispatching email",
            device_id, confidence * 100, mode,
        )
        send_fire_alert_async(event)

    mqtt_client.message_callback_add(topic_filter, _on_alert)
    logger.info(
        "Fire alert handler registered on '%s' (cooldown=%.0fs)",
        topic_filter, state.cooldown,
    )
