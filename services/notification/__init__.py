"""
Notification service — gửi cảnh báo ra ngoài hệ thống.

Hiện hỗ trợ:
  - Gmail SMTP (gmail_alert.py)
  - MQTT alert router (mqtt_alert_handler.py): nhận topic iot/+/alert
    từ ESP32 rồi dispatch email với debounce.

Cách dùng trong main.py:
    from services.notification import register_fire_alert_handler
    register_fire_alert_handler(mqtt_client, env_snapshot_getter=get_latest_env)
"""

from .gmail_alert import send_fire_alert, send_fire_alert_async
from .mqtt_alert_handler import register_fire_alert_handler

__all__ = [
    "send_fire_alert",
    "send_fire_alert_async",
    "register_fire_alert_handler",
]
