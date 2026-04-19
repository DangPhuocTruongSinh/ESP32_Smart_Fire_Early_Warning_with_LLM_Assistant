import threading
import uvicorn

from services.llm_service.chat_api import app
from services.ingestion.mqtt_to_influxdb import mqtt_client, get_latest_env
from services.notification import register_fire_alert_handler


def run_api_server() -> None:
    """Khởi chạy FastAPI server (LLM Chat API + Device Control)."""
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


def run_mqtt_ingestion() -> None:
    """Khởi chạy MQTT subscriber nhận dữ liệu cảm biến từ ESP32."""
    mqtt_client.loop_forever()


if __name__ == "__main__":

    # Đăng ký handler fire alert TRƯỚC khi bắt đầu loop — callback sẽ có
    # hiệu lực ngay từ message đầu tiên sau khi MQTT (re)connect.
    register_fire_alert_handler(mqtt_client, env_snapshot_getter=get_latest_env)

    mqtt_thread = threading.Thread(target=run_mqtt_ingestion, daemon=True, name="mqtt-ingestion")
    mqtt_thread.start()

    run_api_server()
