import json
import time
import ssl
import random
from paho.mqtt.client import Client

# ================= MQTT CONFIG =================
MQTT_HOST = "kingfisher.lmq.cloudamqp.com"
MQTT_PORT = 8883
MQTT_USER = "rowdwwyg:rowdwwyg"
MQTT_PASS = "yUSQiP-iSE2Tm3sXQpp2sDvy7yJGMRzG"
TOPIC = "iot/esp32_01/env"

# ================= CREATE SAMPLE DATA =================
def generate_sample():
    return {
        "device": "ESP32_01",
        "temperature": round(random.uniform(25, 35), 2),
        "humidity": round(random.uniform(50, 80), 2),
        "gas": random.randint(0, 300)
    }

# ================= MQTT CALLBACKS =================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker")
    else:
        print("Connection failed with code", rc)

# ================= MQTT CLIENT =================
client = Client(client_id="mqtt_test_sender")
client.username_pw_set(MQTT_USER, MQTT_PASS)

# TLS for CloudAMQP
client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)

client.on_connect = on_connect

client.connect(MQTT_HOST, MQTT_PORT, 60)
client.loop_start()

print("Sending sample data...")

try:
    while True:
        payload = generate_sample()
        message = json.dumps(payload)

        client.publish(TOPIC, message)
        print("Sent:", message)

        time.sleep(3)

except KeyboardInterrupt:
    print("Stopped by user")
    client.loop_stop()
    client.disconnect()
