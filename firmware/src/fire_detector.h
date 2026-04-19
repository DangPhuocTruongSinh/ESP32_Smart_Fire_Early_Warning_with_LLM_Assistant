/**
 * fire_detector.h — CNN1D TFLite Micro inference engine
 * ======================================================
 * Wraps feature engineering + StandardScaler normalization + TFLite INT8
 * inference into one stateful class suitable for use in FreeRTOS tasks.
 *
 * Feature pipeline (matches training notebook exactly):
 *   1. Base features (10): CO, H2, Humidity, PM0.5, PM1.0, PM_Typical,
 *                          PM_Total, Temperature, UV, VOC
 *   2. Delta features (5): diff vs previous sample for CO/H2/PM05/PM_Total/VOC
 *   3. Rolling features (10): mean + std over last 6 samples (same 5 sensors)
 *   4. Ratio features (3): VOC_CO_ratio, PM_size_ratio, UV_norm
 *   Total: 28 features per timestep
 *
 * Sliding window: 60 timesteps × 28 features (circular buffer).
 * Inference runs every time a new sample is pushed once the buffer is full.
 *
 * Compile condition: only compiled when USE_TFLITE=1 (see platformio.ini).
 */

#pragma once

#ifdef USE_TFLITE

#include <cstring>
#include <cmath>
#include <Arduino.h>

// TFLite Micro
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

// Auto-generated headers (run: python firmware/convert_model.py)
#include "model_data.h"
#include "scaler_params.h"

// ── Tensor arena size ──────────────────────────────────────────────────────
#ifndef TENSOR_ARENA_SIZE
#define TENSOR_ARENA_SIZE (50 * 1024)  // 50 KB — sufficient for CNN1D activations
#endif

// ── Confidence thresholds (from threshold-tuning in notebook) ──────────────
static constexpr float kThresholdFire     = 0.45f;
static constexpr float kThresholdNuisance = 0.45f;

// ── FireDetector ────────────────────────────────────────────────────────────
class FireDetector {
public:
    /**
     * Classification result — matches training label encoding:
     *   Background = 0, Fire = 1, Nuisance = 2
     */
    enum Class : uint8_t { Background = 0, Fire = 1, Nuisance = 2 };

    /**
     * Initialize TFLite interpreter and allocate tensors.
     * Must be called once before push().
     *
     * @return true on success; false if model or arena is too small.
     */
    bool begin() {
        model_ = tflite::GetModel(kCnn1dModelData);
        if (model_->version() != TFLITE_SCHEMA_VERSION) {
            Serial.printf("[FireDetector] Schema mismatch: model=%u sdk=%u\n",
                          model_->version(), TFLITE_SCHEMA_VERSION);
            return false;
        }

        // Register only the ops used by CNN1D to minimize binary size
        static tflite::MicroMutableOpResolver<8> resolver;
        resolver.AddConv2D();
        resolver.AddAveragePool2D();
        resolver.AddMean();               // GlobalAveragePooling1D
        resolver.AddFullyConnected();
        resolver.AddSoftmax();
        resolver.AddReshape();
        resolver.AddMul();                // BatchNorm
        resolver.AddAdd();                // BatchNorm

        static tflite::MicroInterpreter static_interp(
            model_, resolver, tensor_arena_, TENSOR_ARENA_SIZE);
        interpreter_ = &static_interp;

        if (interpreter_->AllocateTensors() != kTfLiteOk) {
            Serial.println("[FireDetector] AllocateTensors failed — increase TENSOR_ARENA_SIZE");
            return false;
        }

        // Verify tensor shapes
        TfLiteTensor* input = interpreter_->input(0);
        if (input->dims->size != 3 ||
            input->dims->data[1] != kWindowSize ||
            input->dims->data[2] != kNumFeatures) {
            Serial.printf("[FireDetector] Unexpected input shape: [%d, %d, %d]\n",
                          input->dims->data[0], input->dims->data[1], input->dims->data[2]);
            return false;
        }

        // Cache quantization params for input tensor
        input_scale_     = input->params.scale;
        input_zero_point_ = input->params.zero_point;

        TfLiteTensor* output = interpreter_->output(0);
        output_scale_      = output->params.scale;
        output_zero_point_ = output->params.zero_point;

        Serial.printf("[FireDetector] Ready — arena used: %u / %u bytes\n",
                      interpreter_->arena_used_bytes(), (unsigned)TENSOR_ARENA_SIZE);
        initialized_ = true;
        return true;
    }

    /**
     * Push one sensor reading into the sliding window.
     * Runs inference automatically when window is full.
     *
     * Parameters (raw sensor values, pre-scaler):
     * @param temp        Temperature_Room (°C)       — DHT11/DHT22
     * @param humidity    Humidity_Room    (%)        — DHT11/DHT22
     * @param co          CO_Room          (ppm)      — MQ-7
     * @param h2          H2_Room          (ppm)      — SGP30 or MQ-8
     * @param pm05        PM0.5_Room       (µg/m³)    — PMS5003
     * @param pm10        PM1.0_Room       (µg/m³)    — PMS5003
     * @param pm_typical  PM_Typical_Size  (µg/m³)    — PMS5003
     * @param pm_total    PM_Total_Room    (µg/m³)    — PMS5003
     * @param uv          UV_Room          (index)    — GUVA-S12SD
     * @param voc         VOC_Room_RAW     (ppb/kOhm) — SGP30
     *
     * @return true if inference was executed this call.
     */
    bool push(float temp, float humidity, float co, float h2,
              float pm05, float pm10, float pm_typical, float pm_total,
              float uv, float voc) {
        // 1 — compute engineered features
        float features[kNumFeatures];
        computeFeatures(temp, humidity, co, h2, pm05, pm10, pm_typical, pm_total, uv, voc, features);

        // 2 — normalize with StandardScaler
        for (int i = 0; i < kNumFeatures; ++i) {
            features[i] = (features[i] - kScalerMean[i]) * kScalerStdInv[i];
        }

        // 3 — write into circular buffer
        memcpy(feature_buf_[head_], features, sizeof(features));
        head_ = (head_ + 1) % kWindowSize;
        if (step_count_ < kWindowSize) ++step_count_;

        // 4 — run inference once window is full
        if (step_count_ >= kWindowSize) {
            runInference();
            return true;
        }
        return false;
    }

    /** @return true once kWindowSize samples have been accumulated. */
    bool isReady() const { return step_count_ >= kWindowSize; }

    /** @return số sample đã tích lũy (0 → kWindowSize). */
    int stepCount() const { return step_count_; }

    /**
     * Feed sensor data into the sliding window WITHOUT running inference.
     * Safe to call before begin() — keeps the buffer warm during Tier-1
     * monitoring so that when CNN is activated, isReady() is already true
     * and inference can start immediately without a warmup period.
     *
     * Parameters: same as push().
     */
    void feedOnly(float temp, float humidity, float co, float h2,
                  float pm05, float pm10, float pm_typical, float pm_total,
                  float uv, float voc) {
        float features[kNumFeatures];
        computeFeatures(temp, humidity, co, h2,
                        pm05, pm10, pm_typical, pm_total, uv, voc, features);
        for (int i = 0; i < kNumFeatures; ++i) {
            features[i] = (features[i] - kScalerMean[i]) * kScalerStdInv[i];
        }
        memcpy(feature_buf_[head_], features, sizeof(features));
        head_ = (head_ + 1) % kWindowSize;
        if (step_count_ < kWindowSize) ++step_count_;
    }

    /** @return true if begin() has been called successfully. */
    bool isInitialized() const { return initialized_; }

    /**
     * Reset sliding window and feature engineering state.
     * The TFLite interpreter stays loaded — no need to call begin() again.
     * Use when reactivating the detector after a period of inactivity.
     */
    void reset() {
        head_       = 0;
        step_count_ = 0;
        roll_head_  = 0;
        roll_count_ = 0;
        uv_max_     = 1e-9f;
        memset(feature_buf_, 0, sizeof(feature_buf_));
        memset(prev_vals_,   0, sizeof(prev_vals_));
        memset(roll_buf_,    0, sizeof(roll_buf_));
        last_class_      = Background;
        last_confidence_ = 0.0f;
        memset(last_probs_, 0, sizeof(last_probs_));
    }

    /** @return last predicted class (Background / Fire / Nuisance). */
    Class getClass() const { return last_class_; }

    /** @return softmax probability of the predicted class [0, 1]. */
    float getConfidence() const { return last_confidence_; }

    /** @return softmax probabilities for all 3 classes. */
    void getProbabilities(float out[kNumClasses]) const {
        memcpy(out, last_probs_, sizeof(last_probs_));
    }

    /** @return human-readable class name. */
    const char* className() const {
        switch (last_class_) {
            case Fire:      return "FIRE";
            case Nuisance:  return "NUISANCE";
            default:        return "Background";
        }
    }

private:
    bool initialized_ = false;

    // ── Feature engineering state ─────────────────────────────────────────
    // Sensors tracked for delta + rolling: CO, H2, PM05, PM_Total, VOC (5 sensors)
    static constexpr int kRollSensors = 5;
    static constexpr int kRollWindow  = 6;   // 1-minute window (6 samples × 10s)

    float prev_vals_[kRollSensors] = {};     // previous sample values
    float roll_buf_[kRollWindow][kRollSensors] = {};  // circular rolling window
    int   roll_head_  = 0;
    int   roll_count_ = 0;
    float uv_max_     = 1e-9f;              // running max for UV normalization

    // ── Sliding window (circular buffer) ─────────────────────────────────
    float feature_buf_[kWindowSize][kNumFeatures] = {};
    int   head_       = 0;
    int   step_count_ = 0;

    // ── TFLite ────────────────────────────────────────────────────────────
    const tflite::Model*       model_       = nullptr;
    tflite::MicroInterpreter*  interpreter_ = nullptr;
    alignas(16) uint8_t        tensor_arena_[TENSOR_ARENA_SIZE];

    float   input_scale_      = 1.0f;
    int32_t input_zero_point_ = 0;
    float   output_scale_     = 1.0f;
    int32_t output_zero_point_ = 0;

    // ── Results ───────────────────────────────────────────────────────────
    Class last_class_      = Background;
    float last_confidence_ = 0.0f;
    float last_probs_[kNumClasses] = {};

    /**
     * Build the 28-feature vector for one timestep.
     * Feature order must exactly match training notebook (feature_cols_indoor.pkl).
     *
     * FEATURE_COLS order:
     *   [0-9]   Base: CO, H2, Humidity, PM05, PM10, PM_Typical, PM_Total, Temp, UV, VOC
     *   [10-14] Delta (diff from prev): CO, H2, PM05, PM_Total, VOC
     *   [15-19] RollMean (last 6): CO, H2, PM05, PM_Total, VOC
     *   [20-24] RollStd (last 6): CO, H2, PM05, PM_Total, VOC
     *   [25]    VOC_CO_ratio = VOC / (|CO| + 0.1)
     *   [26]    PM_size_ratio = PM_Typical / (PM05 + 1)
     *   [27]    UV_norm = UV / (UV_max + 1e-9)
     */
    void computeFeatures(float temp, float humidity, float co, float h2,
                         float pm05, float pm10, float pm_typ, float pm_tot,
                         float uv, float voc, float* out) {
        // Sensor values indexed for rolling: [CO, H2, PM05, PM_Total, VOC]
        float cur[kRollSensors] = {co, h2, pm05, pm_tot, voc};

        // ── Base features [0..9] ──────────────────────────────────────────
        out[0] = co;   out[1] = h2;   out[2] = humidity;
        out[3] = pm05; out[4] = pm10; out[5] = pm_typ;
        out[6] = pm_tot; out[7] = temp; out[8] = uv; out[9] = voc;

        // ── Delta features [10..14] ───────────────────────────────────────
        if (step_count_ == 0) {
            // First sample: delta = 0 (matches pandas .diff().fillna(0))
            for (int i = 0; i < kRollSensors; ++i) out[10 + i] = 0.0f;
        } else {
            for (int i = 0; i < kRollSensors; ++i) {
                out[10 + i] = cur[i] - prev_vals_[i];
            }
        }

        // Update rolling buffer
        memcpy(roll_buf_[roll_head_], cur, sizeof(cur));
        roll_head_ = (roll_head_ + 1) % kRollWindow;
        if (roll_count_ < kRollWindow) ++roll_count_;

        // ── Rolling mean [15..19] & std [20..24] ──────────────────────────
        for (int s = 0; s < kRollSensors; ++s) {
            float sum = 0.0f, sq_sum = 0.0f;
            for (int k = 0; k < roll_count_; ++k) {
                float v = roll_buf_[k][s];
                sum    += v;
                sq_sum += v * v;
            }
            float n    = static_cast<float>(roll_count_);
            float mean = sum / n;
            out[15 + s] = mean;
            // Population std (matches pandas rolling std with min_periods=1, ddof=1
            // → use ddof=0 here as approximation when roll_count < window)
            float var  = (sq_sum / n) - (mean * mean);
            out[20 + s] = (var > 0.0f) ? sqrtf(var) : 0.0f;
        }

        // ── Ratio features [25..27] ───────────────────────────────────────
        out[25] = voc / (fabsf(co) + 0.1f);               // VOC_CO_ratio
        out[26] = pm_typ / (pm05 + 1.0f);                  // PM_size_ratio
        if (uv > uv_max_) uv_max_ = uv;
        out[27] = uv / (uv_max_ + 1e-9f);                  // UV_norm

        // Save current values as previous for next call
        memcpy(prev_vals_, cur, sizeof(cur));
    }

    /**
     * Fill the TFLite input tensor from the circular feature buffer,
     * applying INT8 quantization: q = round(x / scale) + zero_point.
     */
    void fillInputTensor() {
        TfLiteTensor* input = interpreter_->input(0);
        int8_t* data = input->data.int8;

        // Walk the circular buffer in chronological order
        int start = (step_count_ < kWindowSize) ? 0 : head_;
        for (int t = 0; t < kWindowSize; ++t) {
            int src = (start + t) % kWindowSize;
            for (int f = 0; f < kNumFeatures; ++f) {
                float val = feature_buf_[src][f];
                int32_t q = static_cast<int32_t>(
                    roundf(val / input_scale_) + input_zero_point_);
                // Clamp to INT8 range
                if (q < -128) q = -128;
                if (q >  127) q =  127;
                data[t * kNumFeatures + f] = static_cast<int8_t>(q);
            }
        }
    }

    /**
     * Run TFLite Micro inference and update last_class_ / last_confidence_.
     */
    void runInference() {
        fillInputTensor();

        if (interpreter_->Invoke() != kTfLiteOk) {
            Serial.println("[FireDetector] Invoke() failed");
            return;
        }

        // Dequantize output: prob = (q - zero_point) * scale
        TfLiteTensor* output  = interpreter_->output(0);
        const int8_t* out_int8 = output->data.int8;

        int    best_idx   = 0;
        float  best_prob  = -1.0f;
        for (int c = 0; c < kNumClasses; ++c) {
            float prob = (static_cast<float>(out_int8[c]) - output_zero_point_)
                         * output_scale_;
            last_probs_[c] = prob;
            if (prob > best_prob) { best_prob = prob; best_idx = c; }
        }

        // Apply per-class confidence thresholds (from notebook threshold tuning)
        Class predicted = static_cast<Class>(best_idx);
        if (predicted == Fire     && last_probs_[Fire]     < kThresholdFire)     predicted = Background;
        if (predicted == Nuisance && last_probs_[Nuisance] < kThresholdNuisance) predicted = Background;

        last_class_      = predicted;
        last_confidence_ = last_probs_[static_cast<int>(predicted)];
    }
};

#endif  // USE_TFLITE
