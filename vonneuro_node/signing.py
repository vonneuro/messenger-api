"""Request signing between an agent and the hub.

Every agent request carries:
  X-Node-Id         the node's id on the hub (identity; the secret itself is never sent)
  X-Node-Timestamp  Unix time of signing (the hub accepts +-60 s)
  X-Node-Signature  HMAC-SHA256(secret, "METHOD\\nPATH?QUERY\\nTIMESTAMP\\nSHA256(body)")
"""
import hashlib
import hmac
import time

TOLERANCE = 60


def compute(method: str, target: str, body: bytes, timestamp: int, secret: str) -> str:
    message = f"{method.upper()}\n{target}\n{timestamp}\n{hashlib.sha256(body).hexdigest()}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def headers(node_id: int, secret: str, method: str, target: str, body: bytes = b"") -> dict:
    timestamp = int(time.time())
    return {"X-Node-Id": str(node_id), "X-Node-Timestamp": str(timestamp),
            "X-Node-Signature": compute(method, target, body, timestamp, secret)}
