"""
Server-Sent Events wire framing, shared by every endpoint that streams
coarse status updates ahead of (or instead of) a blocking JSON response —
POST /assistant/ask/stream and GET /search/stream today. Kept in one
place so every streaming endpoint emits exactly the same event/data
framing rather than each route reinventing it slightly differently.
"""
import json


def format_sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
