import json
import threading
import time
import ssl
from typing import Any, Dict
from paho.mqtt.client import Client
from influxdb_client_3 import InfluxDBClient3, Point

import os
from dotenv import load_dotenv

load_dotenv()

# ================= Latest env snapshot cache =================
# Giữ bản copy payload env mới nhất để module notification đính kèm
# vào email cảnh báo mà không cần query lại InfluxDB (giảm độ trễ khi cháy).
# Cập nhật trong on_message → truy cập từ thread khác qua get_latest_env().
_latest_env_lock: threading.Lock = threading.Lock()
_latest_env: Dict[str, Any] = {}


def get_latest_env() -> Dict[str, Any]:
    """
    Lấy bản COPY (tránh race) của dữ liệu env mới nhất nhận qua MQTT.

    Returns:
        Dict payload gần nhất (temperature, humidity, gas, và các trường
        bổ sung ở ML mode như co/voc/pm_total/ml_class...). Trả về {} nếu
        chưa có message nào.
    """
    with _latest_env_lock:
        return dict(_latest_env)

# ================= InfluxDB =================
INFLUX_HOST = os.getenv("INFLUX_HOST")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
ORG =  os.getenv("ORG")
BUCKET = os.getenv("BUCKET")


# ================= MQTT =================
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
TOPIC = os.getenv("TOPIC")



influx_client = InfluxDBClient3(
    host=INFLUX_HOST,
    token=INFLUX_TOKEN,
    org=ORG
)

# Pre-populate _latest_env from InfluxDB on startup
try:
    startup_query = """SELECT temperature, humidity, gas, ml_class, ml_confidence, mode, device
            FROM 'environment'
            ORDER BY time DESC
            LIMIT 1"""
    table = influx_client.query(query=startup_query, database=BUCKET, language="sql")
    rows = table.to_pylist()
    if rows:
        with _latest_env_lock:
            _latest_env.update(rows[0])
            _latest_env["_received_at"] = time.time()
        print("✓ Pre-populated _latest_env from InfluxDB:", _latest_env)
except Exception as e:
    print("⚠ Could not pre-populate _latest_env on startup:", e)

# ================= Validation =================
def is_valid(data):
    # Demo playback: skip gas range check (gas = pm_total ~1–10 µg/m³, not ADC 0–4095)
    # Also skip if required fields are missing (e.g. DEMO_COMPLETE sentinel payload)
    try:
        if data.get("mode") == "demo_playback":
            return (
                0 <= data["temperature"] <= 60 and
                0 <= data["humidity"] <= 100
            )
        return (
            0 <= data["temperature"] <= 60 and
            0 <= data["humidity"] <= 100 and
            0 <= data["gas"] <= 4095
        )
    except (KeyError, TypeError):
        return False


# ================= MQTT Callbacks =================
def on_connect(client, userdata, flags, rc):
    print("MQTT connected with code:", rc)
    if rc == 0:
        client.subscribe(TOPIC)
        client.subscribe("device/+/response")
        # Alert từ ESP32 (fire detection) — notification module sẽ xử lý
        # qua message_callback_add pattern, nhưng phải subscribe ở đây để
        # broker thật sự forward message về client.
        client.subscribe("iot/+/alert")
    else:
        print("Connection failed")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())

        # Cache snapshot cho notification SỤM — trước khi validate.
        # Lý do: ML-mode payload không có field 'gas' sẽ fail is_valid(),
        # nhưng vẫn cần đính kèm snapshot đó vào email cảnh báo.
        with _latest_env_lock:
            _latest_env.clear()
            _latest_env.update(payload)
            _latest_env["_received_at"] = time.time()

        if not is_valid(payload):
            print("Invalid data, skipped")
            return

        point = (
            Point("environment")
            .tag("device", payload.get("device", "esp32_01"))
            .field("temperature", float(payload["temperature"]))
            .field("humidity", float(payload["humidity"]))
            .field("gas", int(float(payload.get("gas", 0))))
            .time(time.time_ns())
        )

        # Demo mode: ghi thêm các trường phụ nếu có — dùng cho dashboard visualization
        if payload.get("ground_truth"):
            point = point.field("ground_truth", str(payload["ground_truth"]))
        if payload.get("ml_class"):
            point = point.field("ml_class", str(payload["ml_class"]))
        if payload.get("mode"):
            point = point.tag("mode", str(payload["mode"]))
        if payload.get("ml_confidence") is not None:
            point = point.field("ml_confidence", float(payload["ml_confidence"]))
        if payload.get("demo_step") is not None:
            point = point.field("demo_step", int(payload["demo_step"]))

        influx_client.write(database=BUCKET, record=point)


    except Exception as e:
        print("Error:", e)

def on_disconnect(client, userdata, rc):
    """
    Được gọi tự động khi mất kết nối với broker.

    Args:
        rc: Return code — 0 là ngắt kết nối chủ động, khác 0 là mất đột ngột.
    """
    if rc != 0:
        print(f"⚠ Mất kết nối MQTT đột ngột (rc={rc}). Đang chờ reconnect...")
    # Không gọi client.reconnect() tại đây để tránh exception khi broker vẫn còn down.
    # loop_forever(reconnect_on_failure=True) sẽ tự retry theo reconnect_delay_set().

# ================= MQTT Client =================
mqtt_client = Client(client_id="mqtt_to_influx")
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
mqtt_client.tls_insecure_set(True)

mqtt_client.on_connect    = on_connect
mqtt_client.on_message    = on_message
mqtt_client.on_disconnect = on_disconnect

# Đặt trước connect() để có hiệu lực ngay từ lần kết nối đầu tiên.
# Retry sau 1s, tăng dần theo exponential backoff, tối đa 30s mỗi lần thử.
mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
