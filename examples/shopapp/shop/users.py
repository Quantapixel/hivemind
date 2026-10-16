from .db import fetch_one, execute


def get_user(user_id: str) -> dict | None:
    return fetch_one("SELECT * FROM users WHERE id = ?", user_id)


def find_by_email(email: str) -> dict | None:
    return fetch_one("SELECT * FROM users WHERE email = ?", email)


def create_user(email: str, password_hash: str) -> dict:
    execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", email, password_hash)
    return find_by_email(email)
