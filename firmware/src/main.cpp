#include <Arduino.h>
#include "DHT.h"
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

#define DHTPIN 4
#define DHTTYPE DHT11
#define MQ2_PIN 34

const char *ssid = "PHUOC HOA.";
const char *password = "Hoa1962Mai1968";

const char *mqtt_server = "kingfisher.lmq.cloudamqp.com";
const int mqtt_port = 8883;
const char *mqtt_user = "rowdwwyg:rowdwwyg";
const char *mqtt_password = "yUSQiP-iSE2Tm3sXQpp2sDvy7yJGMRzG";

// Topic publish dữ liệu cảm biến lên server
const char *mqtt_pub_topic = "iot/esp32_01/env";

// Topic nhận lệnh điều khiển từ control_device (dùng wildcard # để bắt tất cả thiết bị)
// Server publish theo format: device/{device_id}/control
const char *mqtt_sub_topic = "device/#";

WiFiClientSecure espClient;
PubSubClient client(espClient);
DHT dht(DHTPIN, DHTTYPE);

// ---------------- MQTT CALLBACK ----------------
/**
 * Được gọi tự động mỗi khi nhận được message từ topic đã subscribe.
 *
 * @param topic   Topic nhận được (vd: "device/led_kitchen/control")
 * @param payload Nội dung message dạng bytes
 * @param length  Độ dài của payload
 */
void onMessageReceived(char *topic, byte *payload, unsigned int length)
{
  Serial.println("Recieve Message");
  Serial.print("Topic  : ");
  Serial.println(topic);

  // Chuyển payload từ bytes sang string để in ra
  String message;
  for (unsigned int i = 0; i < length; i++)
  {
    message += (char)payload[i];
  }
  Serial.print("Payload: ");
  Serial.println(message);
}

// ---------------- WIFI ----------------
void connectToWifi()
{
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
}

// ---------------- MQTT ----------------
void connectToMQTT()
{
  while (!client.connected())
  {
    String clientId = "esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.print("MQTT connecting as ");
    Serial.println(clientId);

    if (client.connect(clientId.c_str(), mqtt_user, mqtt_password))
    {
      Serial.println("Connected to MQTT Broker");

      // Subscribe topic điều khiển sau mỗi lần kết nối thành công
      // (cần subscribe lại vì session có thể bị reset)
      client.subscribe(mqtt_sub_topic);
      Serial.println("Subscribed to: " + String(mqtt_sub_topic));
    }
    else
    {
      Serial.print("MQTT failed, state=");
      Serial.println(client.state());
      delay(5000);
    }
  }
}

void setup()
{
  Serial.begin(115200);
  dht.begin();

  connectToWifi();

  // Cấu hình MQTT trước khi kết nối
  espClient.setInsecure();
  client.setServer(mqtt_server, mqtt_port);
  client.setKeepAlive(60);
  client.setSocketTimeout(15);
  client.setCallback(onMessageReceived);

  connectToMQTT();
}

void loop()
{
  if (!client.connected())
  {
    connectToMQTT();
  }
  client.loop();

  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();
  float gasValue = analogRead(MQ2_PIN);

  char payload[128];
  snprintf(payload, sizeof(payload), "{\"device\":\"ESP32_01\",\"temperature\": %.2f, \"humidity\": %.2f, \"gas\": %.2f}",
           temperature, humidity, gasValue);

  client.publish(mqtt_topic, payload);
  Serial.println(payload);
}
