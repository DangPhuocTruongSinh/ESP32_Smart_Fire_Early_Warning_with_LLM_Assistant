import json
import time
import ssl
from paho.mqtt.client import Client
from influxdb_client_3 import InfluxDBClient3, Point

import os
from dotenv import load_dotenv

load_dotenv()

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

# query = """SELECT *
#         FROM 'environment'
#         WHERE time >= now() - interval '24 hours'
#         ORDER BY time DESC"""

# table = influx_client.query(query=query, database=BUCKET, language="sql")

# ================= Validation =================
def is_valid(data):
    return (
        0 <= data["temperature"] <= 60 and
        0 <= data["humidity"] <= 100 and
        0 <= data["gas"] <= 4095
    )


# ================= MQTT Callbacks =================
def on_connect(client, userdata, flags, rc):
    print("MQTT connected with code:", rc)
    if rc == 0:
        client.subscribe(TOPIC)
    else:
        print("Connection failed")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        # print("Received:", payload)

        if not is_valid(payload):
            print("Invalid data, skipped")
            return

        point = (
            Point("environment")
            .tag("device", payload.get("device", "esp32_01"))
            .field("temperature", float(payload["temperature"]))
            .field("humidity", float(payload["humidity"]))
            .field("gas", int(payload["gas"]))
            .time(time.time_ns())
        )

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
