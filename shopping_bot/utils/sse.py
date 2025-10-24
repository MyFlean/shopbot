from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict


def make_event(event_type: str, data: Dict[str, Any]) -> str:
    """Serialize an SSE event frame with JSON payload."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def heartbeat() -> str:
    return make_event("heartbeat", {"ts": datetime.utcnow().isoformat() + "Z"})


