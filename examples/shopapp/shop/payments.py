from .db import execute


class PaymentError(Exception):
    pass


def charge_card(user_id: str, amount_cents: int, card_token: str) -> str:
    if amount_cents <= 0:
        raise PaymentError("amount must be positive")
    execute("INSERT INTO charges (user_id, amount) VALUES (?, ?)", user_id, amount_cents)
    return f"ch_{user_id}_{amount_cents}"


def refund(charge_id: str) -> None:
    execute("UPDATE charges SET refunded = 1 WHERE id = ?", charge_id)
