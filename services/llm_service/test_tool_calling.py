

import ast
import time
import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from uuid import UUID

import requests
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from langchain_core.callbacks.base import BaseCallbackHandler
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

# Import tools trực tiếp từ chat_api
import services.llm_service.chat_api as _chat_api
from services.llm_service.chat_api import get_sensor_data, control_device

# Patch publish_mqtt trong chat_api để control_device không gửi MQTT thật
_chat_api.publish_mqtt = lambda topic, payload: {
    "success": True,
    "topic": topic,
    "message": "mocked - no real MQTT sent",
}

# ==============================================================
# LẤY MODEL ĐANG CHẠY TRÊN OLLAMA
# ==============================================================

def _get_active_model(fallback: str = "qwen3") -> str:
    """
    Lấy tên model đang được load trên Ollama (hoặc model đầu tiên đã cài).

    Args:
        fallback: Tên model dùng nếu không truy vấn được Ollama.

    Returns:
        Tên model sẽ được dùng cho LLM.
    """
    try:
        resp = requests.get("http://localhost:11434/api/ps", timeout=3)
        models = resp.json().get("models", [])
        if models:
            return models[0]["name"]
    except Exception:
        pass

    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = resp.json().get("models", [])
        if models:
            return models[0]["name"]
    except Exception:
        pass

    return fallback


MODEL_NAME = _get_active_model()

# ==============================================================
# TOOL CALL LOGGER - Dùng LangChain callback để ghi lại tool calls
# ==============================================================

class ToolCallLogger(BaseCallbackHandler):
    """
    Callback handler tự động ghi lại mỗi khi LLM gọi một tool.

    Thuộc tính:
        calls: Danh sách dict {"tool": tên_tool, "args": tham_số}.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: List[Dict[str, Any]] = []

    def clear(self) -> None:
        """Xóa log cũ trước mỗi test case."""
        self.calls.clear()

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """
        Được gọi tự động ngay trước khi tool thực thi.

        Args:
            serialized: Metadata của tool (chứa key "name").
            input_str:  Input truyền vào tool (JSON string hoặc dict).
            run_id:     UUID của lần chạy hiện tại.
        """
        tool_name = serialized.get("name", "unknown")

        if isinstance(input_str, dict):
            args = input_str
        else:
            try:
                # LangChain có thể truyền JSON string hoặc Python repr string
                args = json.loads(input_str)
            except (json.JSONDecodeError, TypeError):
                try:
                    # Fallback: parse Python dict string (dùng nháy đơn)
                    args = ast.literal_eval(input_str)
                except (ValueError, SyntaxError):
                    args = {"input": str(input_str)}

        self.calls.append({"tool": tool_name, "args": args})

# ==============================================================
# TEST CASE DEFINITIONS
# ==============================================================

@dataclass
class TestCase:
    """Định nghĩa một kịch bản kiểm thử."""

    name: str
    description: str
    query: str
    # Tool kỳ vọng LLM gọi - rỗng = kỳ vọng không gọi tool nào
    expected_tools: List[str] = field(default_factory=list)
    # Kiểm tra tham số cụ thể: {tên_tool: {tên_param: giá_trị_kỳ_vọng}}
    expected_args: Optional[Dict[str, Dict]] = None


# Chỉ dùng các tool có trong chat_api: get_sensor_data, control_device
TEST_CASES: List[TestCase] = [
    TestCase(
        name="TC01",
        description="Đọc dữ liệu cảm biến",
        query="Cho tôi xem dữ liệu cảm biến trong nhà.",
        expected_tools=["get_sensor_data"],
    ),
    TestCase(
        name="TC02",
        description="Hỏi về nhiệt độ",
        query="Nhiệt độ trong nhà bây giờ là bao nhiêu?",
        expected_tools=["get_sensor_data"],
    ),
    TestCase(
        name="TC03",
        description="Kiểm tra khí gas có an toàn không",
        query="Khí gas trong nhà có nguy hiểm không? Kiểm tra giúp tôi.",
        expected_tools=["get_sensor_data"],
    ),
    TestCase(
        name="TC04",
        description="Bật đèn bếp",
        query="Bật đèn bếp giúp tôi.",
        expected_tools=["control_device"],
        expected_args={"control_device": {"device_id": "led_kitchen", "action": "on"}},
    ),
    TestCase(
        name="TC05",
        description="Tắt quạt",
        query="Tắt quạt đi.",
        expected_tools=["control_device"],
        expected_args={"control_device": {"device_id": "fan", "action": "off"}},
    ),
    TestCase(
        name="TC06",
        description="Bật máy lạnh phòng ngủ",
        query="Bật máy lạnh phòng ngủ.",
        expected_tools=["control_device"],
        expected_args={"control_device": {"device_id": "ac_bedroom", "action": "on"}},
    ),
    TestCase(
        name="TC07",
        description="Tắt bếp điện",
        query="Tắt bếp điện đi.",
        expected_tools=["control_device"],
        expected_args={"control_device": {"device_id": "stove", "action": "off"}},
    ),
    TestCase(
        name="TC08",
        description="Bật đèn phòng khách",
        query="Bật đèn phòng khách.",
        expected_tools=["control_device"],
        expected_args={"control_device": {"device_id": "led_livingroom", "action": "on"}},
    ),
    TestCase(
        name="TC09",
        description="Câu hỏi thông thường - không cần tool",
        query="Xin chào! Bạn có thể làm gì cho tôi?",
        expected_tools=[],
    ),
    TestCase(
        name="TC10",
        description="Phát hiện nguy hiểm - đọc cảm biến trước khi hành động",
        query="Kiểm tra khói trong nhà, nếu có nguy hiểm hãy tắt bếp ngay.",
        expected_tools=["get_sensor_data"],
    ),
]

# ==============================================================
# TEST RUNNER
# ==============================================================

@dataclass
class TestResult:
    """Kết quả của một test case."""

    test_name: str
    description: str
    passed: bool
    tools_called: List[Dict[str, Any]]
    expected_tools: List[str]
    llm_response: str
    elapsed_ms: float
    fail_reason: str = ""
    error: str = ""


def _evaluate(
    test_case: TestCase, actual_calls: List[Dict[str, Any]]
) -> tuple[bool, str]:
    """
    So sánh tool calls thực tế với kỳ vọng để xác định pass/fail.

    Args:
        test_case:    TestCase chứa expected_tools và expected_args.
        actual_calls: Danh sách tool calls đã được logger ghi lại.

    Returns:
        (passed, fail_reason) - fail_reason rỗng nếu passed=True.
    """
    actual_names = [c["tool"] for c in actual_calls]

    for expected in test_case.expected_tools:
        if expected not in actual_names:
            return False, f"Tool '{expected}' chưa được gọi. Thực tế: {actual_names}"

    if not test_case.expected_tools and actual_names:
        return False, f"LLM không nên gọi tool nhưng đã gọi: {actual_names}"

    if test_case.expected_args:
        for tool_name, expected_args in test_case.expected_args.items():
            call = next((c for c in actual_calls if c["tool"] == tool_name), None)
            if not call:
                return False, f"Không tìm thấy call của tool '{tool_name}'"

            for param, expected_val in expected_args.items():
                actual_val = call["args"].get(param)
                if actual_val != expected_val:
                    return (
                        False,
                        f"Tool '{tool_name}': '{param}' = '{actual_val}' "
                        f"(kỳ vọng '{expected_val}')",
                    )

    return True, ""


def run_test(agent, test_case: TestCase, logger: ToolCallLogger) -> TestResult:
    """
    Chạy một test case và trả về kết quả đánh giá.

    Args:
        agent:     LangGraph ReAct agent đã khởi tạo.
        test_case: TestCase cần kiểm thử.
        logger:    ToolCallLogger để capture tool calls.

    Returns:
        TestResult chứa thông tin pass/fail và chi tiết.
    """
    logger.clear()
    start = time.time()
    llm_response = ""
    error_msg = ""

    try:
        config = {
            "configurable": {"thread_id": f"test_{test_case.name}"},
            "callbacks": [logger],
        }
        result = agent.invoke(
            {"messages": [HumanMessage(content=test_case.query)]},
            config=config,
        )
        llm_response = result["messages"][-1].content
    except Exception as exc:
        error_msg = str(exc)

    elapsed_ms = (time.time() - start) * 1000
    actual_calls = list(logger.calls)
    passed, fail_reason = _evaluate(test_case, actual_calls)

    return TestResult(
        test_name=test_case.name,
        description=test_case.description,
        passed=passed,
        tools_called=actual_calls,
        expected_tools=test_case.expected_tools,
        llm_response=llm_response,
        elapsed_ms=elapsed_ms,
        fail_reason=fail_reason,
        error=error_msg,
    )

# ==============================================================
# OUTPUT FORMATTING
# ==============================================================

SEPARATOR = "─" * 62


def _fmt_call(call: Dict[str, Any]) -> str:
    """Định dạng một tool call dict thành chuỗi dễ đọc."""
    return f"{call['tool']}({json.dumps(call['args'], ensure_ascii=False)})"


def print_result(result: TestResult) -> None:
    """
    In kết quả chi tiết của một test case ra stdout.

    Args:
        result: TestResult cần hiển thị.
    """
    status = "✅ PASS" if result.passed else "❌ FAIL"
    print(f"\n{status}  [{result.test_name}] {result.description}  ({result.elapsed_ms:.0f}ms)")

    exp = ", ".join(result.expected_tools) or "(không có)"
    got = ", ".join(c["tool"] for c in result.tools_called) or "(không có)"
    print(f"  Kỳ vọng tool  : {exp}")
    print(f"  Tool đã gọi   : {got}")

    if result.tools_called:
        print("  Chi tiết calls:")
        for i, call in enumerate(result.tools_called, 1):
            print(f"    [{i}] {_fmt_call(call)}")

    if result.fail_reason:
        print(f"  ⚠ Lý do fail  : {result.fail_reason}")

    if result.error:
        print(f"  ⚠ Lỗi runtime : {result.error}")

    if result.llm_response:
        preview = result.llm_response[:300]
        if len(result.llm_response) > 300:
            preview += " ..."
        print(f"  Phản hồi LLM  : {preview}")


def print_summary(results: List[TestResult]) -> None:
    """
    In bảng tóm tắt toàn bộ kết quả kiểm thử.

    Args:
        results: Danh sách TestResult cần tổng hợp.
    """
    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    avg_ms = sum(r.elapsed_ms for r in results) / total if total else 0

    print(f"\n{'=' * 62}")
    print("  TỔNG KẾT KẾT QUẢ KIỂM THỬ TOOL CALLING")
    print(f"{'=' * 62}")
    print(f"  Tổng số test      : {total}")
    print(f"  ✅ PASS           : {passed}")
    print(f"  ❌ FAIL           : {failed}")
    print(f"  Tỷ lệ thành công  : {passed / total * 100:.1f}%")
    print(f"  Thời gian TB/test : {avg_ms:.0f} ms")

    if failed:
        print(f"\n  Các test FAIL ({failed}):")
        for r in results:
            if not r.passed:
                reason = r.fail_reason or r.error or "Không rõ"
                print(f"    • [{r.test_name}] {r.description}")
                print(f"      → {reason}")

    print(f"{'=' * 62}\n")

# ==============================================================
# MAIN
# ==============================================================

def main() -> None:
    """Khởi chạy toàn bộ bộ test tool calling cho LLM."""
    print(f"\n{'=' * 62}")
    print("  LLM TOOL CALLING TEST SUITE")
    print(f"  Mô hình : Ollama / {MODEL_NAME}")
    print(f"  Tools   : get_sensor_data, control_device  (từ chat_api.py)")
    print(f"  Tổng số : {len(TEST_CASES)} test cases")
    print(f"{'=' * 62}")

    print("\n🔧 Khởi tạo LLM agent...")
    llm = ChatOllama(model=MODEL_NAME, temperature=0)
    tools = [get_sensor_data, control_device]
    agent = create_react_agent(
        model=llm,
        tools=tools,
        checkpointer=InMemorySaver(),
    )
    logger = ToolCallLogger()
    print("✅ Agent sẵn sàng!\n")
    print(SEPARATOR)

    results: List[TestResult] = []
    for i, test_case in enumerate(TEST_CASES, 1):
        print(f"\n[{i:02d}/{len(TEST_CASES)}] {test_case.name}: {test_case.description}")
        print(f"  Query: \"{test_case.query}\"")

        result = run_test(agent, test_case, logger)
        results.append(result)
        print_result(result)
        print(SEPARATOR)

    print_summary(results)


if __name__ == "__main__":
    main()
