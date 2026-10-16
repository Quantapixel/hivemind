from .db import execute, fetch_all


def add_item(user_id: str, sku: str, qty: int = 1) -> None:
    execute("INSERT INTO cart (user_id, sku, qty) VALUES (?, ?, ?)", user_id, sku, qty)


def items(user_id: str) -> list[dict]:
    return fetch_all("SELECT * FROM cart WHERE user_id = ?", user_id)


def cart_total(user_id: str) -> int:
    return sum(i["price"] * i["qty"] for i in items(user_id))


def clear(user_id: str) -> None:
    execute("DELETE FROM cart WHERE user_id = ?", user_id)
