import requests
import json

def test_api():
    url = "http://localhost:8000/analyze"
    
    payload = {
        "question": "Nhiệt độ hiện tại là bao nhiêu?",
        "thread_id": "test_session_1"
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    print(f"Đang gửi request tới {url}...")
    print(f"Câu hỏi: {payload['question']}")
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            result = response.json()
            print("\n--- KẾT QUẢ ---")
            print(f"AI trả lời: {result.get('response')}")
        else:
            print(f"\nLỗi: {response.status_code}")
            print(response.text)
            
    except requests.exceptions.ConnectionError:
        print("\nKhông thể kết nối tới server. Hãy chắc chắn bạn đã chạy 'python chat_api.py' trước.")

if __name__ == "__main__":
    test_api()
