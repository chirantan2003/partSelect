import httpx
import json

def test():
    url = "http://127.0.0.1:8000/chat"
    payload = {
        "messages": [
            {"role": "user", "content": "How can I install part number PS11752778?"}
        ],
        "session_id": "test_session"
    }
    
    print("Sending request to local API...")
    try:
        with httpx.stream("POST", url, json=payload, timeout=20.0) as r:
            print(f"Status Code: {r.status_code}")
            for line in r.iter_lines():
                if line:
                    print(line)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
