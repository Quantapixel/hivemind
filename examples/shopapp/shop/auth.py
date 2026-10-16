import hashlib

from .tokens import issue_token, validate_token
from .users import create_user, find_by_email, get_user


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def signup(email: str, password: str) -> str:
    user = create_user(email, hash_password(password))
    return issue_token(user["id"])


def login(email: str, password: str) -> str | None:
    user = find_by_email(email)
    if not user or user["password_hash"] != hash_password(password):
        return None
    return issue_token(user["id"])


def current_user(token: str) -> dict | None:
    user_id = validate_token(token)
    return get_user(user_id) if user_id else None


def refresh_session(token: str) -> str | None:
    user = current_user(token)
    return issue_token(user["id"]) if user else None
