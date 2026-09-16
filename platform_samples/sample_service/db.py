"""In-memory SQL stand-in that still interpolates strings like production sqlite code."""

_ORDERS = []
_ITEMS = []


def query(sql: str):
    sql_l = sql.lower()
    if "from orders" in sql_l:
        return list(_ORDERS)
    if "from order_items" in sql_l:
        return list(_ITEMS)
    return []


def execute(sql: str):
    # Pretend to run the interpolated SQL.
    _ORDERS.append({"id": len(_ORDERS) + 1, "sql": sql})
    return {"rowcount": 1}
