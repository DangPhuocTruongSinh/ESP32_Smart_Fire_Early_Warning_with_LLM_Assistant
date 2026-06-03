import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # Disable GPU for fair CPU comparison
import sys
import time
import joblib
import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.metrics import f1_score, recall_score, classification_report
import pathlib

ROOT = pathlib.Path(__file__).parent.parent.parent
DATA_PATH = ROOT / "data" / "Indoor Fire Dataset with Distributed Multi-Sensor Nodes.csv"
MODEL_PATH = ROOT / "services" / "ml_service" / "saved_models" / "cnn1d_indoor.keras"
SCALER_PATH = ROOT / "services" / "ml_service" / "saved_models" / "scaler_indoor.pkl"
FEAT_PATH = ROOT / "services" / "ml_service" / "saved_models" / "feature_cols_indoor.pkl"
LABEL_ENC_PATH = ROOT / "services" / "ml_service" / "saved_models" / "label_encoder_indoor.pkl"

WINDOW_SIZE = 18
STRIDE = 2

def add_features(group: pd.DataFrame) -> pd.DataFrame:
    g = group.copy()
    for col in ['CO_Room', 'H2_Room', 'PM05_Room', 'PM_Total_Room', 'VOC_Room_RAW']:
        g[f'delta_{col}']      = g[col].diff().fillna(0)
        g[f'roll_mean_{col}']  = g[col].rolling(6, min_periods=1).mean()
        g[f'roll_std_{col}']   = g[col].rolling(6, min_periods=1).std().fillna(0)
    g['VOC_CO_ratio']   = g['VOC_Room_RAW'] / (g['CO_Room'].abs() + 0.1)
    g['PM_size_ratio']  = g['PM_Room_Typical_Size'] / (g['PM05_Room'] + 1)
    g['UV_norm']        = g['UV_Room'] / (g['UV_Room'].max() + 1e-9)
    return g

def make_windows(X: np.ndarray, y: np.ndarray, window: int = WINDOW_SIZE, stride: int = STRIDE):
    Xs, ys = [], []
    for i in range(0, len(X) - window + 1, stride):
        Xs.append(X[i : i + window])
        ys.append(y[i + window - 1])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.int32)

def apply_future_padding(X: np.ndarray, pad_steps: int, strategy: str = 'last_value') -> np.ndarray:
    X_pad = X.copy()
    if pad_steps <= 0: return X_pad
    valid_steps = X.shape[1] - pad_steps
    if strategy == 'last_value':
        for t in range(valid_steps, X.shape[1]):
            X_pad[:, t, :] = X_pad[:, valid_steps - 1, :]
    elif strategy == 'zero':
        X_pad[:, valid_steps:, :] = 0.0
    return X_pad

def representative_dataset():
    # Sử dụng 100 mẫu ngẫu nhiên từ dữ liệu thực tế (X_test)
    # để TFLite đo đạc dải min/max chính xác cho quá trình lượng tử hoá
    rng = np.random.default_rng(42)
    indices = rng.choice(len(X_test), size=100, replace=False)
    for i in indices:
        x = np.expand_dims(X_test[i], axis=0).astype(np.float32)
        yield [x]

if __name__ == "__main__":
    print("[1] Loading models and artifacts...")
    model_fp32 = tf.keras.models.load_model(str(MODEL_PATH), compile=False)
    scaler = joblib.load(str(SCALER_PATH))
    feature_cols = joblib.load(str(FEAT_PATH))
    le = joblib.load(str(LABEL_ENC_PATH))
    
    print("[2] Loading Test Data...")
    df = pd.read_csv(DATA_PATH)
    # The last node was used for testing (Sensorknoten0016)
    ALL_NODES = sorted(df['Sensor_ID'].unique())
    TEST_NODE = ALL_NODES[-1]
    
    test_df = df[df['Sensor_ID'] == TEST_NODE].sort_values('Date').reset_index(drop=True)
    test_df = add_features(test_df)
    
    X_test_raw = scaler.transform(test_df[feature_cols].fillna(0))
    y_test_raw = le.transform(test_df['ternary_label'])
    
    X_test, y_test = make_windows(X_test_raw, y_test_raw)
    print(f"    Test set shape: {X_test.shape}, {y_test.shape}")
    
    print("[3] Converting Float32 to TFLite INT8...")
    converter = tf.lite.TFLiteConverter.from_keras_model(model_fp32)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    
    tflite_model_int8 = converter.convert()
    interpreter = tf.lite.Interpreter(model_content=tflite_model_int8)
    interpreter.allocate_tensors()
    
    in_idx = interpreter.get_input_details()[0]['index']
    out_idx = interpreter.get_output_details()[0]['index']
    in_scale, in_zero = interpreter.get_input_details()[0]['quantization']
    out_scale, out_zero = interpreter.get_output_details()[0]['quantization']
    
    def predict_tflite_int8(X):
        # Quantize input
        X_q = np.round(X / in_scale + in_zero).astype(np.int8)
        preds = []
        for i in range(len(X_q)):
            interpreter.set_tensor(in_idx, [X_q[i]])
            interpreter.invoke()
            out_q = interpreter.get_tensor(out_idx)[0]
            # Dequantize
            out_fp = (out_q.astype(np.float32) - out_zero) * out_scale
            preds.append(out_fp)
        return np.array(preds)
    
    def evaluate(X, name):
        # Measure Single-Sample (Batch=1) Latency to simulate Edge AI real-time streaming
        if name == 'Float32':
            # Warm-up (TF graph tracing takes extra time on first call)
            _ = model_fp32(X[0:1], training=False)
            
            t0 = time.time()
            preds = []
            for i in range(len(X)):
                out = model_fp32(X[i:i+1], training=False)
                preds.append(out.numpy()[0])
            t1 = time.time()
            y_pred_proba = np.array(preds)
        else:
            t0 = time.time()
            y_pred_proba = predict_tflite_int8(X)
            t1 = time.time()
        
        y_pred = np.argmax(y_pred_proba, axis=1)
        f1 = f1_score(y_test, y_pred, average='macro')
        speed = (t1 - t0) / len(X) * 1000 # ms per sample
        return y_pred, f1, speed
    
    print("\n--- MEMORY (MODEL SIZE) ---")
    keras_size = os.path.getsize(MODEL_PATH)
    converter_fp32 = tf.lite.TFLiteConverter.from_keras_model(model_fp32)
    tflite_model_fp32 = converter_fp32.convert()
    fp32_size = len(tflite_model_fp32)
    int8_size = len(tflite_model_int8)
    print(f"Keras Float32 (.keras) : {keras_size / 1024:.1f} KB")
    print(f"TFLite Float32         : {fp32_size / 1024:.1f} KB")
    print(f"TFLite INT8 Quantized  : {int8_size / 1024:.1f} KB")
    print(f"-> Compression (TFLite FP32 -> INT8): {fp32_size / int8_size:.1f}x")
    
    print("\n--- BASELINE EVALUATION ---")
    _, f1_fp32, speed_fp32 = evaluate(X_test, 'Float32')
    y_pred_int8, f1_int8, speed_int8 = evaluate(X_test, 'INT8')
    
    print(f"Float32 Model -> F1 Macro: {f1_fp32:.4f} | Inference: {speed_fp32:.3f} ms/sample")
    print(f"INT8 TFLite   -> F1 Macro: {f1_int8:.4f} | Inference: {speed_int8:.3f} ms/sample")
    
    print("\n--- STREAMING FIRE RECALL (Padding Robustness) ---")
    pad_steps = 9 # 50% of window
    X_pad = apply_future_padding(X_test, pad_steps, strategy='last_value')
    
    # We only care about Fire recall (class 1)
    # predict
    pred_fp32 = np.argmax(model_fp32.predict(X_pad, batch_size=256, verbose=0), axis=1)
    pred_int8 = np.argmax(predict_tflite_int8(X_pad), axis=1)
    
    rec_fp32 = recall_score(y_test, pred_fp32, labels=[1], average='macro')
    rec_int8 = recall_score(y_test, pred_int8, labels=[1], average='macro')
    
    print(f"Under 50% Padding (9 steps last_value):")
    print(f"Float32 Model -> Streaming Fire Recall: {rec_fp32:.4f}")
    print(f"INT8 TFLite   -> Streaming Fire Recall: {rec_int8:.4f}")
