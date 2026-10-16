from .auth import current_user, login, refresh_session, signup
from .cart import add_item, items
from .orders import cancel_order, checkout


def handle(route: str, body: dict) -> dict:
    if route == "/signup":
        return {"token": signup(body["email"], body["password"])}
    if route == "/login":
        return {"token": login(body["email"], body["password"])}
    if route == "/refresh":
        return {"token": refresh_session(body["token"])}
    if route == "/cart/add":
        user = current_user(body["token"])
        add_item(user["id"], body["sku"], body.get("qty", 1))
        return {"items": items(user["id"])}
    if route == "/checkout":
        return checkout(body["token"], body["card"])
    if route == "/cancel":
        cancel_order(body["token"], body["order"])
        return {"ok": True}
    return {"error": "not found"}
