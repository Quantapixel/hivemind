import hashlib
import hmac
import time

SECRET = b"dev-secret"


def sign(payload: str) -> str:
    return hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: str, ttl: int = 3600) -> str:
    expires = int(time.time()) + ttl
    body = f"{user_id}:{expires}"
    return f"{body}:{sign(body)}"


def validate_token(token: str) -> str | None:
    try:
        user_id, expires, sig = token.rsplit(":", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, sign(f"{user_id}:{expires}")):
        return None
    if int(expires) < time.time():
        return None
    return user_id
