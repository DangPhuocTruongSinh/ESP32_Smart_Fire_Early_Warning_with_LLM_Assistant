import threading
import uvicorn

from services.llm_service.chat_api import app
from services.ingestion.mqtt_to_influxdb import mqtt_client


def run_api_server() -> None:
    """Khởi chạy FastAPI server (LLM Chat API + Device Control)."""
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


def run_mqtt_ingestion() -> None:
    """Khởi chạy MQTT subscriber nhận dữ liệu cảm biến từ ESP32."""
    # reconnect_on_failure=True: tự động retry kết nối theo reconnect_delay_set()
    # khi broker tạm thời không khả dụng, thay vì ném exception và dừng thread.
    mqtt_client.loop_forever()


if __name__ == "__main__":

    mqtt_thread = threading.Thread(target=run_mqtt_ingestion, daemon=True, name="mqtt-ingestion")
    mqtt_thread.start()

    run_api_server()
