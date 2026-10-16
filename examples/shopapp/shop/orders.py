from . import cart
from .auth import current_user
from .db import execute
from .payments import PaymentError, charge_card, refund


def checkout(token: str, card_token: str) -> dict:
    user = current_user(token)
    if not user:
        raise PermissionError("not logged in")
    total = cart.cart_total(user["id"])
    charge_id = charge_card(user["id"], total, card_token)
    try:
        order = place_order(user["id"], charge_id, total)
    except Exception:
        refund(charge_id)
        raise
    cart.clear(user["id"])
    return order


def place_order(user_id: str, charge_id: str, total: int) -> dict:
    execute("INSERT INTO orders (user_id, charge_id, total) VALUES (?, ?, ?)", user_id, charge_id, total)
    return {"user_id": user_id, "charge_id": charge_id, "total": total}


def cancel_order(token: str, order: dict) -> None:
    user = current_user(token)
    if not user or user["id"] != order["user_id"]:
        raise PermissionError("not your order")
    refund(order["charge_id"])
