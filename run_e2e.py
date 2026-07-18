import sys
import json
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

print("Starting E2E investigation on fastapi-users/fastapi-users...", flush=True)

payload = {
    "repo": "https://github.com/fastapi-users/fastapi-users",
    "question": "Trace a bearer token from the incoming HTTP request until it becomes an authenticated user. Which components extract the token, validate it, and load the user, and what happens when the token is invalid?"
}

try:
    with client.stream("POST", "/investigate", json=payload) as response:
        if response.status_code != 200:
            print(f"Error: HTTP {response.status_code}")
            print(response.text)
            sys.exit(1)
            
        print("Connected. Streaming response...\n")
        
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[6:]
                try:
                    event = json.loads(data_str)
                    if "metadata" in event:
                        print(f"[METADATA] Repo: {event['metadata']['repo']} | Lang: {event['metadata'].get('language')}")
                    elif "chunk" in event:
                        print(event["chunk"], end="", flush=True)
                    elif "completed" in event:
                        print("\n\n[COMPLETED]")
                    else:
                        print(f"\n[OTHER] {event}")
                except json.JSONDecodeError:
                    print(f"\n[RAW DATA] {data_str}")
            elif line.startswith("event:"):
                pass
            else:
                print(f"\n[SSE] {line}")
except Exception as e:
    print(f"Exception: {e}")
