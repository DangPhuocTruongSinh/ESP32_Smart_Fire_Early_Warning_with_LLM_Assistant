import time
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent 
from langchain_core.tools import tool
from typing import Literal, Dict, Any
import uvicorn
import json
import paho.mqtt.client as mqtt

from ..ingestion import mqtt_client

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Định nghĩa một tool giả
@tool
def get_sensor_data():
    """Lấy dữ liệu cảm biến (giả lập)"""
    return "Nhiệt độ: 30 độ, Khí gas: Bình thường"

# Device registry
ALLOWED_DEVICES = {
    "stove": {"name": "Bếp điện", "pin": 2, "requires_confirmation": True},
    "ac_bedroom": {"name": "Máy lạnh phòng ngủ", "pin": 4},
    "led_kitchen": {"name": "Đèn bếp", "pin": 5},
    "led_livingroom": {"name": "Đèn phòng khách", "pin": 18},
    "fan": {"name": "Quạt", "pin": 19}
}

device_states = {}

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
        result = mqtt_client.publish(
                topic=topic,
                payload=json.dumps(payload),
                qos=1
            )
    
        # Wait for publish confirmation
        result.wait_for_publish(timeout=2)
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"✅ [MQTT] Published to {topic}")
            print(f"   Payload: {json.dumps(payload, indent=2)}")
            return {
                "success": True,
                "topic": topic,
                "message": "Message sent successfully"
            }
        else:
            print(f"❌ [MQTT] Publish failed: rc={result.rc}")
            return {
                "success": False,
                "error": f"MQTT error code: {result.rc}"
            }
            
    except Exception as e:
        print(f"❌ [MQTT] Exception: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

@tool
def control_device(
    device_id: Literal["stove", "ac_bedroom", "led_kitchen", "led_livingroom", "fan"],
    action: Literal["on", "off"],
) -> Dict[str, Any]:
    """
    Điều khiển thiết bị IoT (bật/tắt).
    
    Args:
        device_id: ID thiết bị (stove, ac_bedroom, led_kitchen, led_livingroom, fan)
        action: Hành động (on hoặc off)
    
    Returns:
        Kết quả thực thi
    """
    
    # Validate device
    if device_id not in ALLOWED_DEVICES:
        return {
            "success": False,
            "error": f"Thiết bị '{device_id}' không tồn tại",
            "allowed_devices": list(ALLOWED_DEVICES.keys())
        }
    
    device_info = ALLOWED_DEVICES[device_id]
    
    # Prepare MQTT payload
    payload = {
        "device_id": device_id,
        "action": action,
        "timestamp": time.time(),
        "pin": device_info["pin"]
    }
        # 4. Publish to MQTT
    topic = f"device/{device_id}/control"
    result = publish_mqtt(topic, payload)
    
    # 5. Update local state if successful
    if result["success"]:
        device_states[device_id] = {
            "state": action,
            "timestamp": payload["timestamp"]
        }
        
        return f"✅ Đã {action} {device_info['name']} thành công!"
    else:
        return f"❌ Không thể điều khiển {device_info['name']}: {result.get('error', 'Unknown error')}"




# system_prompt = """Bạn là một trợ lý thông minh chuyên phân tích dữ liệu cảm biến từ các thiết bị IoT.
#                 Bạn có thể trả lời các câu hỏi về tình trạng hiện tại của thiết bị, xu hướng dữ liệu, và đưa ra dự đoán dựa trên dữ liệu lịch sử.
#                 Hãy sử dụng các công cụ có sẵn để lấy dữ liệu cảm biến khi cần thiết. Luôn cung cấp câu trả lời chi tiết và dễ hiểu cho người dùng."""

tools = [get_sensor_data, control_device]

# Ollama + Langchain for chatbot
llm = ChatOllama(model="gwen3")
memory = InMemorySaver()

agent = create_agent(model=llm, tools=tools, checkpointer=memory)

class ChatRequest(BaseModel):
    question: str
    thread_id: str = "1"
class ControlRequest(BaseModel):
    device_id: str
    action: Literal["on", "off"]

# API endpoint
@app.post("/analyze")
async def analyze(req: ChatRequest):
    user_question = req.question
    try:
        # LangGraph yêu cầu config chứa thread_id để biết nhớ vào đâu
        config = {"configurable": {"thread_id": req.thread_id}}
        
        # Invoke agent
        response = await agent.ainvoke(
            {"messages": [("user", user_question)]},
            config=config
        )
        
        return {"response": response["messages"][-1].content}
    except Exception as e:
        return {"error": str(e)}

@app.post("/control/direct")
async def direct_control(req: ControlRequest):
    """
    Điều khiển trực tiếp MQTT (bypass LLM) - dùng để test.
    
    Example:
    POST /control/direct
    {
        "device_id": "led_kitchen",
        "action": "on"
    }
    """
    device_id = req.device_id
    action = req.action
    
    # Validate
    if device_id not in ALLOWED_DEVICES:
        return {
            "success": False,
            "error": f"Device '{device_id}' not found",
            "allowed_devices": list(ALLOWED_DEVICES.keys())
        }
    
    if action not in ["on", "off"]:
        return {
            "success": False,
            "error": "Action must be 'on' or 'off'"
        }
    
    device_info = ALLOWED_DEVICES[device_id]
    
    # Prepare payload
    payload = {
        "device_id": device_id,
        "action": action,
        "timestamp": time.time(),
        "pin": device_info["pin"]
    }
    
    # Publish
    topic = f"device/{device_id}/control"
    result = publish_mqtt(topic, payload)
    
    if result["success"]:
        device_states[device_id] = {
            "state": action,
            "timestamp": payload["timestamp"]
        }
        
        return {
            "success": True,
            "message": f"Published to {topic}",
            "payload": payload
        }
    else:
        return {
            "success": False,
            "error": result.get("error", "Unknown error")
        }


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
            "pin": info["pin"],
            "state": state,
            "requires_confirmation": info.get("requires_confirmation", False)
        })
    
    return {"devices": devices}

if __name__ == "__main__":
    # Chạy server tại localhost:8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
