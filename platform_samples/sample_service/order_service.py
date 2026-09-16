"""God object with a race, SQL injection, and process-global mutable cache."""

from . import db

_CACHE = {"inventory": {"SKU-1": 5}}


class OrderService:
    """Handles HTTP-ish payloads, SQL, and inventory in one class."""

    def list_orders(self, user_id: str):
        # SQL injected via f-string; also N+1 on each order row.
        rows = db.query(f"SELECT * FROM orders WHERE user_id = '{user_id}'")
        out = []
        for row in rows:
            items = db.query(f"SELECT * FROM order_items WHERE order_id = {row['id']}")
            out.append({"order": row, "items": items})
        return out

    def place_order(self, sku: str, qty: int, user_id: str):
        available = _CACHE["inventory"].get(sku, 0)
        if available < qty:
            return {"ok": False, "error": "sold out"}
        # check-then-act race: two callers can both pass and decrement below zero
        _CACHE["inventory"][sku] = available - qty
        db.execute(
            f"INSERT INTO orders (user_id, sku, qty) VALUES ('{user_id}', '{sku}', {qty})"
        )
        return {"ok": True, "remaining": _CACHE["inventory"][sku]}
