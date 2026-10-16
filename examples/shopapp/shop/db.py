_rows: dict[str, list[dict]] = {}


def fetch_one(sql: str, *args) -> dict | None:
    rows = fetch_all(sql, *args)
    return rows[0] if rows else None


def fetch_all(sql: str, *args) -> list[dict]:
    return _rows.get(sql, [])


def execute(sql: str, *args) -> None:
    _rows.setdefault(sql, []).append({"args": args})
