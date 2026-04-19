/**
 * ============================================================
 *  Fire Detection — ESP32 FreeRTOS Architecture
 * ============================================================
 *  Hai chế độ biên dịch (chọn qua platformio.ini):
 *
 *  [env:esp32dev]         USE_RULE_BASED=1
 *    → Rule-based (gas > 1500 || temp > 60°C), phần cứng hiện tại
 *
 *  [env:esp32dev-ml]      USE_TFLITE=1
 *    → CNN1D TFLite INT8 inference, cần sensor đầy đủ
 *      Yêu cầu thêm: MQ-7 (CO), SGP30 (VOC/H2), PMS5003 (PM), UV sensor
 *
 *  Tasks:
 *  [T1] taskSensorPublish — đọc sensor mỗi SAMPLE_INTERVAL_MS
 *                           tích lũy window → chạy CNN1D → publish MQTT
 *                           Priority: HIGH (3)   Core: 1
 *
 *  [T2] taskMQTTReceive   — giữ kết nối broker, poll incoming,
 *                           xử lý lệnh điều khiển GPIO
 *                           Priority: NORMAL (2) Core: 0
 * ============================================================
 */

#include <Arduino.h>
#include "DHT.h"
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#ifdef USE_TFLITE
#include "fire_detector.h"
// Sensor thêm
#include <Adafruit_SGP30.h>
#include <Adafruit_PM25AQI.h>
#endif

// ─────────────────────────────────────────────
//  Pin & hardware config
// ─────────────────────────────────────────────
#define DHTPIN 4
#define DHTTYPE DHT11
#define MQ2_PIN 34 // Gas (rule-based mode)
#define MQ7_PIN 35 // CO analog (ML mode) — thêm MQ-7
#define UV_PIN 32  // UV analog (ML mode) — thêm GUVA-S12SD

// Actuator pins
#define STOVE_PIN 27
#define FAN_PIN 26
#define AC_PIN 25

// I2C pins (SGP30)
#define I2C_SDA 21
#define I2C_SCL 22

// UART2 (PMS5003)
#define PMS_RX 16
#define PMS_TX 17

// ─────────────────────────────────────────────
//  Network & MQTT config
// ─────────────────────────────────────────────
const char *WIFI_SSID = "Slime 2.4GHz";
const char *WIFI_PASSWORD = "0934116403";

const char *MQTT_SERVER = "kingfisher.lmq.cloudamqp.com";
const int MQTT_PORT = 8883;
const char *MQTT_USER = "rowdwwyg:rowdwwyg";
const char *MQTT_PASSWORD = "yUSQiP-iSE2Tm3sXQpp2sDvy7yJGMRzG";

const char *TOPIC_PUB_ENV = "iot/esp32_01/env";
const char *TOPIC_PUB_ALERT = "iot/esp32_01/alert";
const char *TOPIC_SUB_CMD = "device/+/control";

// ─────────────────────────────────────────────
//  Timing config
// ─────────────────────────────────────────────
#ifdef USE_TFLITE
// CNN1D cần đủ 18 bước, lấy mẫu mỗi 5s → 90 giây lịch sử trước khi inference lần đầu
static constexpr TickType_t SAMPLE_INTERVAL_MS = pdMS_TO_TICKS(5000);
#else
static constexpr TickType_t SAMPLE_INTERVAL_MS = pdMS_TO_TICKS(1000);
static constexpr TickType_t PUBLISH_INTERVAL_MS = pdMS_TO_TICKS(5000);
#endif

// ─────────────────────────────────────────────
//  FreeRTOS objects
// ─────────────────────────────────────────────
static SemaphoreHandle_t mqttMutex;

// ─────────────────────────────────────────────
//  Global clients
// ─────────────────────────────────────────────
static WiFiClientSecure espClient;
static PubSubClient mqttClient(espClient);
static DHT dht(DHTPIN, DHTTYPE);

#ifdef USE_TFLITE
static FireDetector    fireDetector;
static Adafruit_SGP30  sgp30;
static Adafruit_PM25AQI pmsAQI;
static HardwareSerial  pmsSerial(2); // UART2 for PMS5003

// ── 2-tier detection state machine ───────────────────────────────────────
enum DetectionState : uint8_t {
    STATE_TIER1_ONLY = 0, // Rule-based only, CNN inactive — trạng thái bình thường
    STATE_CNN_WARMUP,     // Rule triggered, CNN đang tích lũy buffer (18 samples)
    STATE_CNN_ACTIVE,     // CNN running inference mỗi sample
};

static DetectionState detState      = STATE_TIER1_ONLY;
static int            bgConsecutive = 0; // đếm liên tiếp Background để deactivate CNN

// ── Tier-1 thresholds (data-driven từ Indoor Fire Dataset, 305K rows) ────────
//
// PM_Total: Background p99 = 28 µg/m³  → threshold 30 bắt 89.8% fire events
//           Đây là trigger đáng tin cậy nhất (Cohen d = 1.11 Fire vs BG)
static constexpr float kPmTotalAlert  =  30.0f; // µg/m³  (PMS5003)

// CO: Dataset dùng sensor điện hóa calibrated (BG mean=0.07 ppm, Fire=1.10 ppm).
//     MQ-7 trong firmware dùng rough linear calibration 0–1000 ppm từ ADC.
//     Hai thang đo KHÔNG so sánh trực tiếp được — threshold này chỉ là
//     safety net cho trường hợp CO rất cao (đám cháy lớn, dây điện chập).
//     Cần recalibrate MQ-7 với R0 thực tế trước khi dùng chính xác.
static constexpr float kCoAlertPpm    =  50.0f; // ppm MQ-7 rough (chưa calibrate)

// Temperature: Trong dataset, Fire max temp ≈ 29°C (sensor đặt xa nguồn nhiệt).
//              Threshold nhiệt không đáng tin cậy cho indoor fire detection.
//              Vẫn giữ làm safety net cho trường hợp sensor gắn gần nguồn nhiệt.
static constexpr float kTempAlertC    =  40.0f; // °C — chỉ trigger khi sensor rất gần lửa

// Số sample Background liên tiếp trước khi CNN quay về sleep
static constexpr int   kCnnDeactivate =       6; // 6 × 5s = 30s không có threat → tắt CNN
#endif

// ─────────────────────────────────────────────
//  WiFi helper
// ─────────────────────────────────────────────
static void connectWifi()
{
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("[WiFi] Connecting");
    while (WiFi.status() != WL_CONNECTED)
    {
        vTaskDelay(pdMS_TO_TICKS(500));
        Serial.print(".");
    }
    Serial.printf("\n[WiFi] Connected — IP: %s\n", WiFi.localIP().toString().c_str());
}

// ─────────────────────────────────────────────
//  MQTT callback
// ─────────────────────────────────────────────
static void onMessageReceived(char *topic, byte *payload, unsigned int length)
{
    if (strstr(topic, "/control") == nullptr)
        return;

    Serial.printf("[MQTT] Recv topic: %s\n", topic);

    JsonDocument doc;
    if (deserializeJson(doc, payload, length) != DeserializationError::Ok)
    {
        Serial.println("[MQTT] JSON parse error");
        return;
    }

    const char *device_id = doc["device_id"];
    const char *action = doc["action"];
    if (!device_id || !action)
        return;

    bool turnOn = (String(action) == "on");
    uint8_t state = turnOn ? HIGH : LOW;
    bool success = false;

    if (strcmp(device_id, "ac") == 0)
    {
        digitalWrite(AC_PIN, state);
        success = true;
    }
    else if (strcmp(device_id, "fan") == 0)
    {
        digitalWrite(FAN_PIN, state);
        success = true;
    }
    else if (strcmp(device_id, "stove") == 0)
    {
        digitalWrite(STOVE_PIN, state);
        success = true;
    }

    Serial.printf("[CMD] device=%s action=%s success=%d\n", device_id, action, success);

    char resTopic[64];
    snprintf(resTopic, sizeof(resTopic), "device/%s/response", device_id);
    char resPayload[128];
    snprintf(resPayload, sizeof(resPayload),
             success ? "{\"device_id\":\"%s\",\"status\":\"success\",\"action\":\"%s\"}"
                     : "{\"device_id\":\"%s\",\"status\":\"error\",\"action\":\"%s\"}",
             device_id, action);
    mqttClient.publish(resTopic, resPayload);
}

// ─────────────────────────────────────────────
//  TASK 1a — Sensor + Publish (USE_RULE_BASED)
// ─────────────────────────────────────────────
#ifdef USE_RULE_BASED
static void taskSensorPublish(void *pvParam)
{
    Serial.println("[T1] DHT11 warmup (2s)...");
    vTaskDelay(pdMS_TO_TICKS(2000));
    Serial.println("[T1] DHT11 ready [RULE-BASED mode]");

    float sumTemp = 0, sumHum = 0, sumGas = 0;
    int count = 0;
    TickType_t xLastWake = xTaskGetTickCount();
    TickType_t publishTick = xTaskGetTickCount() + PUBLISH_INTERVAL_MS;

    for (;;)
    {
        float t = dht.readTemperature();
        float h = dht.readHumidity();
        float g = (float)analogRead(MQ2_PIN);

        if (!isnan(t) && !isnan(h))
        {
            sumTemp += t;
            sumHum += h;
            sumGas += g;
            count++;
            Serial.printf("[T1] Sample %d — T:%.1f H:%.1f Gas:%.0f\n", count, t, h, g);
        }

        if (xTaskGetTickCount() >= publishTick)
        {
            publishTick = xTaskGetTickCount() + PUBLISH_INTERVAL_MS;
            if (count > 0)
            {
                float avgT = sumTemp / count;
                float avgH = sumHum / count;
                float avgG = sumGas / count;

                // Simple threshold rule (original logic)
                // MQ2 ADC: 0–4095, ~1500 là mức gas cao bất thường
                // Temp: dataset cho thấy 40°C đã bất thường với indoor sensor
                bool fireAlert = (avgG > 1500) || (avgT > 40.0f);

                char payload[256];
                snprintf(payload, sizeof(payload),
                         "{\"device\":\"ESP32_01\","
                         "\"temperature\":%.2f,\"humidity\":%.2f,\"gas\":%.2f,"
                         "\"fire_alert\":%s,\"mode\":\"rule_based\"}",
                         avgT, avgH, avgG, fireAlert ? "true" : "false");

                Serial.printf("[T1] Publish: %s\n", payload);

                if (xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(300)) == pdTRUE)
                {
                    if (mqttClient.connected())
                    {
                        mqttClient.publish(TOPIC_PUB_ENV, payload);
                        if (fireAlert)
                        {
                            mqttClient.publish(TOPIC_PUB_ALERT, "{\"status\":\"FIRE_DETECTED\",\"mode\":\"rule_based\"}");
                            Serial.println("[T1] *** FIRE ALERT (rule-based) ***");
                        }
                    }
                    xSemaphoreGive(mqttMutex);
                }

                sumTemp = sumHum = sumGas = 0;
                count = 0;
            }
        }

        vTaskDelayUntil(&xLastWake, SAMPLE_INTERVAL_MS);
    }
}
#endif // USE_RULE_BASED

// ─────────────────────────────────────────────
//  TASK 1b — Sensor + CNN1D Inference (USE_TFLITE)
// ─────────────────────────────────────────────
#ifdef USE_TFLITE
static void taskSensorPublish(void *pvParam)
{
    // ── Sensor initialization ─────────────────────────────────────────────
    Serial.println("[T1] DHT11 warmup (2s)...");
    vTaskDelay(pdMS_TO_TICKS(2000));

    Wire.begin(I2C_SDA, I2C_SCL);
    if (!sgp30.begin())
    {
        Serial.println("[T1][WARN] SGP30 not found — VOC/H2 will be 0");
    }
    else
    {
        Serial.println("[T1] SGP30 ready");
        vTaskDelay(pdMS_TO_TICKS(15000)); // SGP30 cần 15s ổn định sau power-on
    }

    pmsSerial.begin(9600, SERIAL_8N1, PMS_RX, PMS_TX);
    if (!pmsAQI.begin_UART(&pmsSerial))
    {
        Serial.println("[T1][WARN] PMS5003 not found — PM values will be 0");
    }
    else
    {
        Serial.println("[T1] PMS5003 ready");
    }

    Serial.println("[T1] 2-tier detection active — Tier1 (rule-based) watching");

    TickType_t xLastWake = xTaskGetTickCount();

    for (;;)
    {
        // ── Read DHT11 ────────────────────────────────────────────────────
        float temp     = dht.readTemperature();
        float humidity = dht.readHumidity();
        if (isnan(temp) || isnan(humidity))
        {
            Serial.println("[T1] DHT read error — skipping sample");
            vTaskDelayUntil(&xLastWake, SAMPLE_INTERVAL_MS);
            continue;
        }

        // ── Read MQ-7 CO (rough calibration: 0–1000 ppm linear) ──────────
        float co_ppm = ((float)analogRead(MQ7_PIN) / 4095.0f) * 1000.0f;

        // ── Read UV (GUVA-S12SD: 0–15 UV index) ──────────────────────────
        float uv_idx = ((float)analogRead(UV_PIN) / 4095.0f) * 15.0f;

        // ── Read SGP30 (VOC + H2) ─────────────────────────────────────────
        float voc_ppb = 0.0f, h2_ppm = 0.0f;
        if (sgp30.IAQmeasureRaw())
        {
            voc_ppb = (float)sgp30.rawTVOC;
            h2_ppm  = (float)sgp30.rawH2;
        }

        // ── Read PMS5003 ──────────────────────────────────────────────────
        float pm05 = 0.0f, pm10 = 0.0f, pm_typical = 0.0f, pm_total = 0.0f;
        PM25_AQI_Data pms_data = {};
        if (pmsAQI.read(&pms_data))
        {
            pm05      = (float)pms_data.pm10_standard;
            pm10      = (float)pms_data.pm25_standard;
            pm_typical = (float)pms_data.pm100_standard;
            pm_total   = pm05 + pm10 + pm_typical;
        }

        // ── Tier-1: threshold check (luôn chạy, chi phí gần như 0) ───────
        // pm_total: trigger chính — bắt 89.8% fire events trong dataset (BG p99=28)
        // co_ppm:   backup — MQ-7 rough calibration, cần recalibrate
        // temp:     safety net cuối cùng — chỉ khi sensor gắn rất gần nguồn nhiệt
        bool rule_alert = (pm_total   > kPmTotalAlert)
                       || (co_ppm     > kCoAlertPpm)
                       || (temp       > kTempAlertC);

        static const char* const kStateNames[] = {"TIER1", "WARMUP", "CNN"};
        Serial.printf("[T1][%s] T:%.1f H:%.1f CO:%.0f VOC:%.0f H2:%.0f "
                      "PM:%.0f UV:%.2f\n",
                      kStateNames[detState],
                      temp, humidity, co_ppm, voc_ppb, h2_ppm, pm_total, uv_idx);

        // ── Tier-2 state machine ──────────────────────────────────────────
        bool        ran_inference = false;
        bool        fire_alert    = rule_alert; // Tier-1 luôn được tính
        const char* mode_str      = "rule_only";

        switch (detState)
        {
        // ── TIER1_ONLY: feedOnly() giữ buffer luôn warm, không infer ─────
        case STATE_TIER1_ONLY:
            // Luôn feed vào buffer (không infer) — buffer sẵn sàng ngay khi cần
            fireDetector.feedOnly(
                temp, humidity, co_ppm, h2_ppm,
                pm05, pm10, pm_typical, pm_total, uv_idx, voc_ppb);

            if (rule_alert)
            {
                // Khởi tạo TFLite interpreter lần đầu (lazy — chỉ một lần duy nhất)
                if (!fireDetector.isInitialized())
                {
                    Serial.println("[T1] Threshold exceeded — initializing CNN1D...");
                    if (!fireDetector.begin())
                    {
                        Serial.println("[T1][ERROR] FireDetector init failed — staying TIER1");
                        break;
                    }
                }

                bgConsecutive = 0;

                // Nếu buffer đã đầy (boot > 90s) → bỏ qua warmup, infer ngay
                if (fireDetector.isReady())
                {
                    detState = STATE_CNN_ACTIVE;
                    Serial.println("[T1] Buffer warm → CNN_ACTIVE immediately");
                }
                else
                {
                    detState = STATE_CNN_WARMUP;
                    Serial.printf("[T1] → CNN_WARMUP (%d/%d samples)\n",
                                  fireDetector.stepCount(), kWindowSize);
                }
            }
            break;

        // ── CNN_WARMUP: chỉ xảy ra trong 90s đầu sau boot ────────────────
        case STATE_CNN_WARMUP:
            ran_inference = fireDetector.push(
                temp, humidity, co_ppm, h2_ppm,
                pm05, pm10, pm_typical, pm_total, uv_idx, voc_ppb);
            mode_str = "cnn_warmup";
            if (ran_inference)
            {
                detState = STATE_CNN_ACTIVE;
                Serial.println("[T1] → CNN_ACTIVE");
            }
            break;

        // ── CNN_ACTIVE: inference mỗi 5s ─────────────────────────────────
        case STATE_CNN_ACTIVE:
            ran_inference = fireDetector.push(
                temp, humidity, co_ppm, h2_ppm,
                pm05, pm10, pm_typical, pm_total, uv_idx, voc_ppb);
            mode_str   = "cnn1d_tflite";
            fire_alert = rule_alert;
            if (ran_inference)
            {
                bool cnn_fire = (fireDetector.getClass() == FireDetector::Fire);
                fire_alert    = cnn_fire || rule_alert;

                if (!cnn_fire && !rule_alert)
                {
                    // Không có threat → đếm ngược deactivate
                    if (++bgConsecutive >= kCnnDeactivate)
                    {
                        // Không reset buffer — tiếp tục feedOnly() để giữ warm
                        detState      = STATE_TIER1_ONLY;
                        bgConsecutive = 0;
                        Serial.println("[T1] Threat cleared → CNN deactivated, buffer kept warm");
                    }
                }
                else
                {
                    bgConsecutive = 0;
                }
            }
            break;
        }

        // ── Build MQTT payload ────────────────────────────────────────────
        char payload[400];
        if (ran_inference && detState == STATE_CNN_ACTIVE)
        {
            float probs[3];
            fireDetector.getProbabilities(probs);
            snprintf(payload, sizeof(payload),
                     "{\"device\":\"ESP32_01\","
                     "\"temperature\":%.2f,\"humidity\":%.2f,"
                     "\"co\":%.1f,\"voc\":%.1f,\"h2\":%.1f,"
                     "\"pm05\":%.1f,\"pm10\":%.1f,\"pm_total\":%.1f,"
                     "\"uv\":%.2f,"
                     "\"ml_class\":\"%s\","
                     "\"ml_confidence\":%.3f,"
                     "\"prob_bg\":%.3f,\"prob_fire\":%.3f,\"prob_nuis\":%.3f,"
                     "\"fire_alert\":%s,"
                     "\"mode\":\"%s\"}",
                     temp, humidity,
                     co_ppm, voc_ppb, h2_ppm,
                     pm05, pm10, pm_total, uv_idx,
                     fireDetector.className(),
                     fireDetector.getConfidence(),
                     probs[0], probs[1], probs[2],
                     fire_alert ? "true" : "false",
                     mode_str);
        }
        else
        {
            snprintf(payload, sizeof(payload),
                     "{\"device\":\"ESP32_01\","
                     "\"temperature\":%.2f,\"humidity\":%.2f,"
                     "\"co\":%.1f,\"voc\":%.1f,\"h2\":%.1f,"
                     "\"pm05\":%.1f,\"pm10\":%.1f,"
                     "\"uv\":%.2f,"
                     "\"ml_class\":\"%s\","
                     "\"fire_alert\":%s,"
                     "\"mode\":\"%s\"}",
                     temp, humidity, co_ppm, voc_ppb, h2_ppm, pm05, pm10, uv_idx,
                     detState == STATE_CNN_WARMUP ? "warming_up" : "Background",
                     fire_alert ? "true" : "false",
                     mode_str);
        }

        Serial.printf("[T1] Publish: %s\n", payload);

        if (xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(300)) == pdTRUE)
        {
            if (mqttClient.connected())
            {
                mqttClient.publish(TOPIC_PUB_ENV, payload);

                if (fire_alert)
                {
                    char alert[160];
                    bool cnn_confirmed = ran_inference &&
                                         fireDetector.getClass() == FireDetector::Fire;
                    if (cnn_confirmed)
                    {
                        snprintf(alert, sizeof(alert),
                                 "{\"status\":\"FIRE_DETECTED\","
                                 "\"confidence\":%.3f,\"mode\":\"cnn1d_tflite\"}",
                                 fireDetector.getConfidence());
                        Serial.printf("[T1] *** FIRE ALERT (CNN1D %.1f%%) ***\n",
                                      fireDetector.getConfidence() * 100);
                    }
                    else
                    {
                        snprintf(alert, sizeof(alert),
                                 "{\"status\":\"FIRE_DETECTED\","
                                 "\"confidence\":1.0,\"mode\":\"%s\"}",
                                 mode_str);
                        Serial.printf("[T1] *** FIRE ALERT (%s) ***\n", mode_str);
                    }
                    mqttClient.publish(TOPIC_PUB_ALERT, alert);
                }
            }
            xSemaphoreGive(mqttMutex);
        }

        vTaskDelayUntil(&xLastWake, SAMPLE_INTERVAL_MS);
    }
}
#endif // USE_TFLITE

// ─────────────────────────────────────────────
//  TASK 2 — MQTT Receive (shared by both modes)
// ─────────────────────────────────────────────
static void taskMQTTReceive(void *pvParam)
{
    for (;;)
    {
        if (!mqttClient.connected())
        {
            String clientId = "esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
            Serial.printf("[T2] MQTT connecting as %s ...\n", clientId.c_str());

            if (xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(5000)) == pdTRUE)
            {
                bool ok = mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASSWORD);
                if (ok)
                {
                    mqttClient.subscribe(TOPIC_SUB_CMD);
                    Serial.println("[T2] MQTT connected & subscribed");
                }
                else
                {
                    Serial.printf("[T2] MQTT failed, state=%d\n", mqttClient.state());
                }
                xSemaphoreGive(mqttMutex);
            }

            if (!mqttClient.connected())
            {
                vTaskDelay(pdMS_TO_TICKS(5000));
                continue;
            }
        }

        if (xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(50)) == pdTRUE)
        {
            mqttClient.loop();
            xSemaphoreGive(mqttMutex);
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup()
{
    Serial.begin(115200);

    // Actuator GPIO
    for (uint8_t pin : {(uint8_t)AC_PIN, (uint8_t)FAN_PIN, (uint8_t)STOVE_PIN})
    {
        pinMode(pin, OUTPUT);
        digitalWrite(pin, LOW);
    }

    dht.begin();
    connectWifi();

    espClient.setInsecure();
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setKeepAlive(60);
    mqttClient.setSocketTimeout(1);
    mqttClient.setCallback(onMessageReceived);

    mqttMutex = xSemaphoreCreateMutex();
    configASSERT(mqttMutex);

#ifdef USE_TFLITE
    // CNN khởi tạo lazy — chỉ gọi begin() khi Tier-1 threshold bị vượt lần đầu.
    // Không chiếm CPU lúc boot, tensor_arena_ đã được cấp phát tĩnh trong RAM.
    Serial.printf("[Setup] 2-tier mode ready — RAM free: %u KB\n",
                  ESP.getFreeHeap() / 1024);
#endif

#ifdef USE_RULE_BASED
    Serial.println("[Setup] Rule-based mode (no TFLite)");
#endif

    //                        func              name           stack   param  prio  handle  core
    xTaskCreatePinnedToCore(taskSensorPublish, "SensorPublish", 16384, NULL, 3, NULL, 1);
    xTaskCreatePinnedToCore(taskMQTTReceive, "MQTTReceive", 8192, NULL, 2, NULL, 0);

    Serial.println("[Setup] 2 FreeRTOS tasks started");
}

// ─────────────────────────────────────────────
//  LOOP — không dùng, logic đã chuyển vào tasks
// ─────────────────────────────────────────────
void loop()
{
    vTaskDelay(pdMS_TO_TICKS(10000));
}
