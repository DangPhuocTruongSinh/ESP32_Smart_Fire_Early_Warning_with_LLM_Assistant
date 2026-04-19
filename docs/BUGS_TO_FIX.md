# Danh sách lỗi cần sửa

Ghi lại các lỗi đã phát hiện nhưng **chưa sửa** trong lần implement chức năng
Gmail alert. Các lỗi này có thể khiến model CNN **nhận diện sai** hoặc làm
pipeline dữ liệu **mất data** dù code vẫn compile & chạy bình thường.

Mức độ: 🔴 Blocker · 🟠 Cao · 🟡 Trung bình · 🟢 Thấp

---

## 🔴 1. Mapping kênh PMS5003 bị sai khái niệm

**Vị trí:** `firmware/src/main.cpp` (phần đọc PMS5003 trong `taskSensorPublish`
mode `USE_TFLITE`).

Code hiện tại gán:

```cpp
pm05      = (float)pms_data.pm10_standard;   // ← thực ra là PM1.0
pm10      = (float)pms_data.pm25_standard;   // ← thực ra là PM2.5
pm_typical = (float)pms_data.pm100_standard; // ← thực ra là PM10
```

Tên trường trong thư viện `Adafruit_PM25AQI`:

| Field trong struct          | Ý nghĩa thực |
|-----------------------------|-------------|
| `pm10_standard`             | **PM1.0** (µg/m³) |
| `pm25_standard`             | **PM2.5** (µg/m³) |
| `pm100_standard`            | **PM10**  (µg/m³) |
| `particles_05um`            | Số hạt ≥ 0.5 µm / 0.1 L (gần nhất với khái niệm PM0.5) |

Dataset `Indoor Fire` trained với `PM0.5_Room` — PMS5003 **không đo trực tiếp**
chỉ số này. Phải chọn một trong các hướng:

- [ ] Retrain model bỏ feature `PM0.5` hoặc thay bằng PM1.0.
- [ ] Đổi sang sensor đo PM0.5 trực tiếp (PMS7003 cũng không có, cần sensor laser chuyên).
- [ ] Dùng `particles_05um` nếu dataset train cũng dùng cùng đơn vị — cần
      verify trong notebook gốc.

**Ảnh hưởng:** Feature PM0.5 lệch thang đo, sau khi StandardScaler normalize
sẽ rơi ngoài phân phối training → softmax trả về xác suất không tin cậy.

---

## 🔴 2. SGP30 dùng `IAQmeasureRaw()` thay vì `IAQmeasure()`

**Vị trí:** `firmware/src/main.cpp` phần đọc SGP30.

```cpp
if (sgp30.IAQmeasureRaw()) {
    voc_ppb = (float)sgp30.rawTVOC;
    h2_ppm  = (float)sgp30.rawH2;
}
```

`rawTVOC` và `rawH2` là **raw ADC count** từ cảm biến (số nguyên lớn, không có
đơn vị ppb/ppm). Tên biến `voc_ppb` và `h2_ppm` gây hiểu nhầm.

Nếu dataset train thu bằng sensor TVOC calibrated (đơn vị ppb) thì feature
đang lệch vài bậc. Sau StandardScaler sẽ bị clamp về cực trị trong
quantization INT8 → Conv1D tính trên giá trị bão hoà.

**Cần xác minh trong notebook `fire_models_indoor.ipynb`:**

- [ ] `VOC_Room_RAW` trong dataset là raw count hay đã calibrate sang ppb?
- [ ] Nếu calibrated → đổi sang `sgp30.IAQmeasure()` lấy `TVOC` và `eCO2`.
- [ ] Nếu raw → giữ nguyên `IAQmeasureRaw()` nhưng **đảm bảo** cảm biến cùng
      model trong cả 2 môi trường.

---

## 🟠 3. Rolling std sai bậc tự do (ddof)

**Vị trí:** `firmware/src/fire_detector.h` — hàm `computeFeatures()`.

```cpp
float var  = (sq_sum / n) - (mean * mean);   // population variance (ddof=0)
out[20 + s] = (var > 0.0f) ? sqrtf(var) : 0.0f;
```

Pandas `Series.rolling().std()` mặc định dùng `ddof=1` (sample std). Hai công
thức khác nhau theo hệ số `sqrt(n / (n-1))`:

- `n = 2` → lệch ~41 %
- `n = 3` → lệch ~22 %
- `n = 6` → lệch ~10 %

Với `min_periods=1` lúc buffer chưa đầy, sai lệch càng lớn ở những bước đầu
sau khi reset.

**Cách sửa (chọn 1):**

- [ ] Trên device: `if (n > 1) out[20+s] *= sqrtf(n / (n - 1.0f));`
- [ ] Retrain notebook với `.rolling(6, min_periods=1).std(ddof=0)`.

---

## 🟠 4. `UV_norm` running max gây drift theo thời gian

**Vị trí:** `firmware/src/fire_detector.h` — cuối hàm `computeFeatures()`.

```cpp
if (uv > uv_max_) uv_max_ = uv;
out[27] = uv / (uv_max_ + 1e-9f);
```

`uv_max_` **đơn điệu tăng** suốt runtime → `UV_norm` giảm dần ngay cả khi UV
thực không đổi. Trong training, `UV_norm = UV / UV.max()` thường được tính
trên toàn bộ dataset, nên giá trị cố định.

Sau `reset()`, `uv_max_ = 1e-9f` → lần đo đầu tiên cho `UV_norm ≈ 1.0` (giá
trị cực trị), lệch xa phân phối training.

**Cách sửa (chọn 1):**

- [ ] Hardcode `kUvMax` bằng max trong training set (đọc từ notebook).
- [ ] Nếu notebook tính `UV_norm` theo node/ngày thì cần window max tương ứng
      trên device (expensive).

---

## 🟠 5. `SAMPLE_INTERVAL_MS = 5000` có thể không khớp stride training

**Vị trí:** `firmware/src/main.cpp`.

```cpp
static constexpr TickType_t SAMPLE_INTERVAL_MS = pdMS_TO_TICKS(5000);
```

Comment ở `platformio.ini` và notebook gốc nói `WINDOW_SIZE = 60 × 10 s`. Nhưng
model hiện tại có `window=18` → đã retrain với cấu hình khác (chưa rõ stride).
Feature `diff` và `rolling(6)` phụ thuộc **trực tiếp** vào khoảng thời gian
giữa 2 sample: nếu train 10 s mà deploy 5 s thì delta/rolling đều lệch.

**Việc cần làm:**

- [ ] Mở `services/ml_service/fire_models_indoor.ipynb`, tìm đoạn
      `resample('10s')` hoặc tương tự để xác nhận stride.
- [ ] Đồng bộ `SAMPLE_INTERVAL_MS` trên ESP32 với stride đó.
- [ ] Nếu muốn sample 5 s cho tier-1 mượt hơn thì chạy CNN ở 10 s (downsample
      trước khi push vào FireDetector).

---

## 🟡 6. MQ-7 CO chưa calibrate R0

**Vị trí:** `firmware/src/main.cpp`.

```cpp
float co_ppm = ((float)analogRead(MQ7_PIN) / 4095.0f) * 1000.0f;
```

Đây là linear mapping thô, **không phải công thức datasheet MQ-7** (log-log
với hệ số Rs/R0). Dataset dùng sensor điện hoá calibrated — thang đo hoàn toàn
khác.

Hệ quả: CO feature sau StandardScaler gần như luôn bão hoà về cực trị.

**Cách sửa:**

- [ ] Để ESP32 chạy 24h trong không khí sạch để tìm R0.
- [ ] Áp công thức datasheet: `ppm = a * (Rs / R0)^b` với a, b từ biểu đồ.
- [ ] Comment trong code (kCoAlertPpm) đã xác nhận điểm này — nhưng vẫn chưa
      calibrate.

---

## 🟡 7. ML-mode payload thiếu field `gas` → `is_valid()` drop message

**Vị trí:** `services/ingestion/mqtt_to_influxdb.py` — hàm `is_valid()`.

```python
def is_valid(data):
    return (
        0 <= data["temperature"] <= 60 and
        0 <= data["humidity"] <= 100 and
        0 <= data["gas"] <= 4095          # ← KeyError khi ML mode không gửi 'gas'
    )
```

ESP32 ở mode `USE_TFLITE` publish payload có `co`, `voc`, `h2`, `pm_total`,
... **không có** `gas`. Khi đó `is_valid(payload)` raise `KeyError`, bị `try/
except` nuốt → dữ liệu ML mode **không bao giờ** vào InfluxDB.

Cache snapshot cho email cảnh báo **đã được đặt TRƯỚC khi gọi `is_valid`**
trong lần sửa này (commit "Gmail alert"), nên email vẫn đính kèm được snapshot.
Nhưng InfluxDB pipeline vẫn hỏng.

**Cách sửa:**

- [ ] Dùng `data.get("gas", 0)` + nới lỏng range, hoặc
- [ ] Tách 2 hàm validate: `is_valid_rule_based()` vs `is_valid_ml()` dựa
      vào field `mode` trong payload, và
- [ ] Mở rộng schema Point InfluxDB để ghi thêm `co`, `pm_total`, `ml_class`,
      `ml_confidence` khi có.

---

## 🟢 8. MQTT publish dưới mutex với TLS handshake

**Vị trí:** `firmware/src/main.cpp` — `taskSensorPublish` mode ML.

Khi `fire_alert == true`, 2 publish liên tiếp (`TOPIC_PUB_ENV` và
`TOPIC_PUB_ALERT`) được gọi **bên trong** `xSemaphoreTake(mqttMutex, 300ms)`.
Nếu TLS phải re-handshake có thể kéo dài hơn 300 ms → semaphore timeout,
message bị drop, đồng thời `taskMQTTReceive` bị chặn xử lý command.

Không blocker nhưng cần theo dõi khi triển khai thật.

---

## Tài liệu tham chiếu

- `services/ml_service/fire_models_indoor.ipynb` — training pipeline gốc.
- `firmware/convert_model.py` — pipeline convert Keras → TFLite INT8.
- `firmware/src/fire_detector.h` — feature engineering on-device.

Khi sửa bất kỳ item nào ở trên, **tick checkbox** và note lại ngày + commit
hash để dễ truy vết.
