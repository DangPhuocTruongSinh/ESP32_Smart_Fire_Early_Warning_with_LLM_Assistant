import time
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent 
from langchain_core.tools import tool
from typing import Literal, Dict, Any, List
from functools import wraps
import json
import requests
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
import uuid

# Lấy các model đang CHẠY (loaded vào RAM)
response = requests.get("http://localhost:11434/api/ps")
models = response.json()
model_name = models["models"][0]["name"]
print("Models: ", model_name)

import os
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()
BUCKET = os.getenv("BUCKET")

from services.ingestion import mqtt_client, influx_client
from services.llm_service.log import log_user_message, log_tool_calls, log_assistant_response, log_error

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Định nghĩa Tool lấy dữ liệu cảm biến
@tool
def get_sensor_data() -> str:
    """
    Lấy dữ liệu cảm biến (Nhiệt độ, Độ ẩm, Khí Gas) mới nhất từ InfluxDB.
    
    Returns:
        Chuỗi chứa thông tin các chỉ số cảm biến hiện tại.
    """
    try:
        # Truy vấn điểm dữ liệu mới nhất (latest data point) trong vòng 5 phút qua
        query = f"""
        SELECT *
        FROM 'environment'
        WHERE time >= now() - interval '5 minutes'
        ORDER BY time DESC
        LIMIT 1
        """
        
        table = influx_client.query(query=query, database=BUCKET, language="sql")
        
        # Chuyển đổi kết quả (PyArrow Table) thành danh sách các dictionary (mỗi dict là một hàng)
        results = table.to_pylist()
        
        if not results:
            return "Không tìm thấy dữ liệu cảm biến nào trong 5 phút qua. Thiết bị có thể đang mất kết nối."
            
        latest_data = results[0]
        
        temp = latest_data.get('temperature', 'N/A')
        hum = latest_data.get('humidity', 'N/A')
        gas = latest_data.get('gas', 'N/A')
        
        # Đánh giá mức độ nguy hiểm của Gas (Tùy chỉnh ngưỡng theo thực tế cảm biến MQ2 của bạn)
        gas_status = "Bình thường"
        if isinstance(gas, (int, float)):
            if gas > 2000: # Ví dụ ngưỡng nguy hiểm
                gas_status = "Nguy hiểm (Phát hiện rò rỉ hoặc cháy)"
            elif gas > 1000:
                gas_status = "Cảnh báo (Nồng độ gas cao)"
                
        return f"Dữ liệu hiện tại: Nhiệt độ {temp}°C, Độ ẩm {hum}%, Khí Gas {gas} ({gas_status})."
        
    except Exception as e:
        return f"Lỗi khi truy xuất dữ liệu cảm biến: {str(e)}"

# Device registry
ALLOWED_DEVICES = {
    "stove": {"name": "Bếp điện", "requires_confirmation": True},
    "ac": {"name": "Máy lạnh"},
    "fan": {"name": "Quạt" }
}

# Khởi tạo trạng thái "off" cho tất cả thiết bị để khớp với GPIO LOW trong setup() của ESP32.
# Các trạng thái có thể: "on", "off", "uncertain" (lệnh đã gửi nhưng timeout — không rõ ESP32 có thực thi không).
device_states: Dict[str, Any] = {
    dev_id: {"state": "off", "timestamp": None}
    for dev_id in ALLOWED_DEVICES
}

MQTT_ACK_WAIT_SECONDS = 0.15

pending_controls = {}

def on_control_response(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        device_id = payload.get("device_id")
        if device_id in pending_controls:
            event, response_data = pending_controls[device_id]
            response_data.update(payload)
            event.set()
    except Exception as e:
        print("Error parsing response:", e)

mqtt_client.message_callback_add("device/+/response", on_control_response)

def format_timestamp_iso(unix_timestamp: float) -> str:
    """
    Chuyển Unix timestamp sang định dạng ISO 8601 (UTC) dễ đọc cho người dùng.

    Args:
        unix_timestamp: Thời gian dạng Unix timestamp (giây).

    Returns:
        Chuỗi thời gian ISO 8601, ví dụ: 2026-02-26T03:20:00Z.
    """
    return datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_mqtt(topic: str, payload: Dict[str, Any]) -> Dict[str, Any]:    
    """
    Gửi message qua MQTT broker.
    
    Args:
        topic: MQTT topic (vd: "device/led_kitchen/control")
        payload: Dictionary chứa data cần gửi
    
    Returns:
        Dict chứa status và message
    """
    try:
        # Với kiến trúc 1 MQTT client, loop/reconnect chỉ được quản lý tại main.py.
        # API chỉ publish và fail-fast khi client chưa connected để tránh reconnect storm.
        if not mqtt_client.is_connected():
            return {
                "success": False,
                "error": "MQTT not connected"
            }

        result = mqtt_client.publish(
                topic=topic,
                payload=json.dumps(payload),
                qos=1
            )

        if result.rc != 0:
            return {
                "success": False,
                "error": f"Publish failed with rc={result.rc}"
            }

        # Chờ ACK rất ngắn để giảm TTFB của API control.
        try:
            result.wait_for_publish(timeout=MQTT_ACK_WAIT_SECONDS)
        except Exception:
            return {
                "success": True,
                "topic": topic,
                "message": "Message queued successfully"
            }

        if result.is_published():
            return {
                "success": True,
                "topic": topic,
                "message": "Message sent successfully (PUBACK received)"
            }

        return {
            "success": True,
            "topic": topic,
            "message": "Message queued successfully"
        }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

TZ = ZoneInfo("Asia/Ho_Chi_Minh")

scheduler = BackgroundScheduler(timezone="Asia/Ho_Chi_Minh")
scheduler.start()

@app.on_event("shutdown")
def shutdown_scheduler():
    scheduler.shutdown()


def schedulable(func):
    """
    Decorator thêm khả năng hẹn giờ cho bất kỳ callable nào.

    Nếu được gọi với delay_seconds > 0 hoặc run_at_iso != "", lời gọi sẽ
    được đặt lịch qua APScheduler thay vì thực thi ngay lập tức.

    Args thêm vào hàm được bọc:
        delay_seconds (int): Số giây trì hoãn. 0 = thực thi ngay.
        run_at_iso (str): Thời điểm cụ thể ISO 8601 (không có timezone,
                          mặc định Asia/Ho_Chi_Minh).
                          Nếu giờ đã qua trong ngày → tự dời sang ngày mai.

    Returns:
        Kết quả của hàm gốc nếu thực thi ngay, hoặc dict xác nhận
        {"scheduled": True, "job_id": ..., "run_at": ...} nếu hẹn giờ.
    """
    @wraps(func)
    def wrapper(*args, delay_seconds: int = 0, run_at_iso: str = "", **kwargs):
        if delay_seconds == 0 and not run_at_iso:
            return func(*args, **kwargs)

        now = datetime.now(tz=TZ)

        if delay_seconds > 0:
            run_time = now + timedelta(seconds=delay_seconds)
        else:
            try:
                run_time = datetime.fromisoformat(run_at_iso).replace(tzinfo=TZ)
                if run_time <= now:
                    run_time += timedelta(days=1)
            except ValueError:
                return {"error": f"❌ Định dạng thời gian không hợp lệ: '{run_at_iso}'"}

        job_id = f"{func.__name__}_{uuid.uuid4().hex[:6]}"
        scheduler.add_job(
            func=func,
            trigger=DateTrigger(run_date=run_time),
            args=args,
            kwargs=kwargs,
            id=job_id,
            replace_existing=False,
            misfire_grace_time=60,
        )

        run_time_str = run_time.strftime("%H:%M ngày %d/%m/%Y")
        return {"scheduled": True, "job_id": job_id, "run_at": run_time_str}

    return wrapper


class DeviceCommand(BaseModel):
    device_id: str
    action: Literal["on", "off"]

@schedulable
def control_device_impl(commands: List[DeviceCommand]) -> Dict[str, Any]:
    """Core logic điều khiển thiết bị — dùng chung cho tool và scheduler."""
    results = {}
    events_to_wait = []

    for cmd in commands:
        device_id = cmd.device_id
        action = cmd.action

        if device_id not in ALLOWED_DEVICES:
            results[device_id] = f"❌ Thiết bị '{device_id}' không tồn tại"
            continue

        device_info = ALLOWED_DEVICES[device_id]
        event_time = time.time()
        payload = {
            "device_id": device_id,
            "device_name": device_info["name"],
            "action": action,
            "timestamp": event_time,
            "timestamp_iso": format_timestamp_iso(event_time),
        }

        event = threading.Event()
        response_data = {}
        pending_controls[device_id] = (event, response_data)
        events_to_wait.append({
            "device_id": device_id,
            "device_name": device_info["name"],
            "action": action,
            "event": event,
            "response_data": response_data,
            "payload": payload,
        })

        publish_result = publish_mqtt(f"device/{device_id}/control", payload)
        if not publish_result["success"]:
            pending_controls.pop(device_id, None)
            events_to_wait.pop()
            results[device_id] = f"❌ Lỗi gửi lệnh {device_info['name']}: {publish_result.get('error')}"

    timeout_limit = 5.0
    start_time = time.time()

    for item in events_to_wait:
        device_id = item["device_id"]
        time_left = timeout_limit - (time.time() - start_time)
        got_response = item["event"].wait(timeout=max(time_left, 0))
        pending_controls.pop(device_id, None)

        if got_response:
            if item["response_data"].get("status") == "success":
                # ESP32 xác nhận thực thi thành công — cập nhật trạng thái chắc chắn.
                device_states[device_id] = {
                    "state": item["action"],
                    "timestamp": item["payload"]["timestamp"],
                }
                results[device_id] = f"✅ Đã {item['action']} {item['device_name']} thành công!"
            else:
                # ESP32 báo lỗi — lệnh không được thực thi, giữ nguyên trạng thái cũ.
                results[device_id] = (
                    f"❌ Lỗi từ {item['device_name']}: "
                    f"{item['response_data'].get('message', 'Unknown error')}"
                )
        else:
            # Timeout — lệnh đã gửi tới broker nhưng không nhận được phản hồi từ ESP32.
            # ESP32 có thể đã thực thi (response bị mất) hoặc chưa (thiết bị mất kết nối).
            # Đánh dấu "uncertain" để get_device_status có thể cảnh báo người dùng.
            device_states[device_id]["state"] = "uncertain"
            results[device_id] = (
                f"⏳ Timeout: Không nhận được phản hồi từ {item['device_name']}. "
                "Trạng thái thiết bị không chắc chắn — hãy kiểm tra thực tế."
            )

    return results

@tool
def control_device(
    commands: List[DeviceCommand],
    delay_seconds: int = 0,
    run_at_iso: str = "",
) -> Dict[str, Any]:
    """
    Bật/tắt một hoặc nhiều thiết bị IoT, có thể thực thi ngay hoặc hẹn giờ.

    Args:
        commands: Danh sách lệnh, mỗi lệnh gồm device_id (ac, fan, stove) và action (on/off).
                  Ví dụ: [{"device_id": "ac", "action": "on"}]
        delay_seconds: Số giây trì hoãn trước khi thực thi (0 = ngay lập tức).
                       Ví dụ: "10 phút nữa" → delay_seconds=600
        run_at_iso: Thời điểm cụ thể ISO 8601, múi giờ Asia/Ho_Chi_Minh (chỉ dùng khi delay_seconds=0).
                    Ví dụ: "lúc 6h tối" → "2026-04-16T18:00:00"
                    Nếu giờ đã qua trong ngày → tự động dời sang ngày mai.

    Returns:
        - Thực thi ngay: dict kết quả từng thiết bị.
        - Hẹn giờ: {"scheduled": True, "job_id": ..., "run_at": ...}
    """
    return control_device_impl(commands, delay_seconds=delay_seconds, run_at_iso=run_at_iso)




@tool
def get_device_status() -> str:
    """
    Kiểm tra trạng thái bật/tắt của tất cả thiết bị trong nhà.
    Dùng khi người dùng hỏi về tình trạng các thiết bị, ví dụ:
    "Các thiết bị trong nhà đã tắt chưa?", "Bếp có đang bật không?",
    "Thiết bị nào đang bật?", "Nhà có thiết bị nào còn hoạt động không?".

    Returns:
        Chuỗi liệt kê trạng thái từng thiết bị (bật/tắt/không chắc).
    """
    STATE_LABELS = {
        "on":        "Đang BẬT",
        "off":       "Đã TẮT",
        "uncertain": "Không chắc (lệnh đã gửi nhưng ESP32 không phản hồi)",
    }

    lines = [
        f"- {info['name']}: {STATE_LABELS.get(device_states[dev_id]['state'], 'Chưa rõ')}"
        for dev_id, info in ALLOWED_DEVICES.items()
    ]

    return "Trạng thái thiết bị:\n" + "\n".join(lines)


system_prompt = """Bạn là một trợ lý thông minh chuyên phân tích dữ liệu cảm biến và điều khiển thiết bị IoT.
Bạn có thể trả lời các câu hỏi về tình trạng hiện tại của thiết bị, xu hướng dữ liệu, và đưa ra dự đoán dựa trên dữ liệu lịch sử.
Hãy sử dụng các công cụ có sẵn để lấy dữ liệu cảm biến khi cần thiết. Luôn cung cấp câu trả lời chi tiết và dễ hiểu cho người dùng.

Mỗi tin nhắn từ người dùng sẽ được đính kèm dòng [Thời gian hiện tại: ...] ở đầu — đây là thời gian thực tế lúc họ gửi tin. Hãy dùng thông tin này khi xử lý lệnh hẹn giờ.

Khi điều khiển thiết bị, hãy dùng tool `control_device` với các quy tắc sau:
- Thực thi NGAY: để delay_seconds=0 và run_at_iso="" (mặc định).
- Hẹn giờ theo KHOẢNG THỜI GIAN ("10 phút nữa", "sau 1 tiếng"): đặt delay_seconds bằng số giây tương ứng.
  Ví dụ: "10 phút nữa" → delay_seconds=600, "sau 2 tiếng" → delay_seconds=7200.
- Hẹn giờ theo GIỜ CỤ THỂ: đặt run_at_iso theo định dạng "YYYY-MM-DDTHH:MM:SS".
  Nếu giờ đã qua trong ngày, hệ thống sẽ tự động dời sang ngày mai.
- Khi người dùng nói giờ MÀ KHÔNG RÕ sáng/tối (vd: "6 giờ", "8h"), hãy chọn lần xuất hiện tiếp theo trong tương lai:
  * Nếu giờ đó chưa đến trong ngày → dùng ngày hôm nay.
  * Nếu cả AM lẫn PM đều đã qua → dùng ngày mai giờ đó.
  Ví dụ: hiện tại 14:00, user nói "6 giờ" → chọn 18:00 hôm nay (không phải 06:00 đã qua).

Khi người dùng hỏi về trạng thái thiết bị (vd: "Các thiết bị đã tắt chưa?", "Bếp có đang bật không?", "Nhà còn thiết bị nào bật không?"), hãy dùng tool `get_device_status` để lấy thông tin trạng thái hiện tại."""

tools = [get_sensor_data, control_device, get_device_status]

# Ollama + Langchain for chatbot
llm = ChatOllama(model=model_name)
memory = InMemorySaver()

agent = create_agent(model=llm, system_prompt=system_prompt, tools=tools, checkpointer=memory)

class ChatRequest(BaseModel):
    question: str
    thread_id: str = "1"
class ControlRequest(BaseModel):
    commands: List[DeviceCommand]


# API endpoint
@app.post("/analyze")
async def analyze(req: ChatRequest):
    now = datetime.now(TZ)
    # Inject thời gian thực vào đầu mỗi message để LLM phân giải đúng
    # các biểu thức giờ mơ hồ như "6 giờ" (sáng hay tối?).
    time_context = now.strftime("[Thời gian hiện tại: %H:%M %A %d/%m/%Y]\n")
    user_message = time_context + req.question

    log_user_message(req.thread_id, req.question)

    try:
        config = {"configurable": {"thread_id": req.thread_id}}
        response = await agent.ainvoke(
            {"messages": [("user", user_message)]},
            config=config
        )

        log_tool_calls(req.thread_id, response["messages"])

        answer = response["messages"][-1].content
        log_assistant_response(req.thread_id, answer)

        return {"response": answer}
    except Exception as e:
        log_error(req.thread_id, e)
        return {"error": str(e)}

@app.post("/control/direct")
async def direct_control(req: ControlRequest):
    """
    Điều khiển trực tiếp MQTT (bypass LLM) - dùng để test.
    
    Example:
    POST /control/direct
    {
        "commands": [
            {
                "device_id": "ac",
                "action": "on"
            },
            {
                "device_id": "fan",
                "action": "off"
            }
        ]
    }
    """
    
    # Chúng ta có thể tận dụng lại logic của Tool để không bị lặp code
    results = control_device.invoke({"commands": req.commands})
    
    # Định dạng lại response cho chuẩn REST API
    all_success = all("✅" in msg for msg in results.values())
    
    return {
        "success": all_success,
        "results": results
    }


@app.get("/sensor/history")
async def sensor_history(minutes: int = 30):
    """
    Trả về time series dữ liệu cảm biến (nhiệt độ, độ ẩm, khí gas) theo từng phút,
    dùng để vẽ chart trực tiếp trong UI mà không cần Grafana iframe.

    Args:
        minutes: Khoảng thời gian cần lấy dữ liệu tính từ hiện tại (mặc định 30 phút).

    Returns:
        JSON với danh sách các điểm dữ liệu theo thứ tự thời gian tăng dần.
        Mỗi điểm có dạng: { time, temperature, humidity, gas }
    """
    try:
        query = f"""
        SELECT
            DATE_BIN(INTERVAL '1 minute', time, TIMESTAMP '1970-01-01 00:00:00') AS time,
            AVG(temperature) AS temperature,
            AVG(humidity)    AS humidity,
            AVG(gas)         AS gas
        FROM 'environment'
        WHERE time >= now() - interval '{minutes} minutes'
        GROUP BY 1
        ORDER BY 1 ASC
        """

        table = influx_client.query(query=query, database=BUCKET, language="sql")
        rows = table.to_pylist()

        data = []
        for row in rows:
            ts = row.get("time")
            # PyArrow timestamps can be datetime objects or integers (nanoseconds)
            if hasattr(ts, "isoformat"):
                time_str = ts.isoformat().replace("+00:00", "Z")
            elif isinstance(ts, (int, float)):
                time_str = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                time_str = str(ts)

            data.append({
                "time":        time_str,
                "temperature": round(row["temperature"], 1) if row.get("temperature") is not None else None,
                "humidity":    round(row["humidity"], 1)    if row.get("humidity")    is not None else None,
                "gas":         round(row["gas"])            if row.get("gas")         is not None else None,
            })

        return {"data": data, "minutes": minutes}

    except Exception as e:
        return {"error": str(e), "data": []}


@app.get("/devices/status")
async def list_devices():
    """
    Xem trạng thái tất cả thiết bị.
    
    Example:
    GET /devices/status
    """
    devices = []
    for dev_id, info in ALLOWED_DEVICES.items():
        state = device_states.get(dev_id, {}).get("state", "unknown")
        devices.append({
            "id": dev_id,
            "name": info["name"],
            "state": state,
            "requires_confirmation": info.get("requires_confirmation", False)
        })
    
    return {"devices": devices}

