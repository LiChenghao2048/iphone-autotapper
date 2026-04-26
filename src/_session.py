import requests

WDA_URL = "http://127.0.0.1:8100"


def get_or_create_session() -> str:
    """Create a new WDA session (works on whatever app is on screen)."""
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid
