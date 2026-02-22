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
const char *mqtt_topic = "iot/esp32_01/env";

WiFiClientSecure espClient;
PubSubClient client(espClient);
DHT dht(DHTPIN, DHTTYPE);

// ---------------- WIFI ----------------
void connectToWifi()
{
  WiFi.begin((ssid), (password));
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
  }
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
  connectToMQTT();

  espClient.setInsecure();

  client.setServer(mqtt_server, mqtt_port);
  client.setKeepAlive(60);
  client.setSocketTimeout(15);
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
