"""The demo business database used by the sample Text2SQL agent.

Sample content, not platform schema: these are the ``customers`` / ``orders`` /
``products`` / ``order_items`` / ``sales_metrics`` tables the bundled Text2SQL
MCP server queries. Application tables (``query_feedback``) live with their
feature and are created by ``scripts/setup_platform.py``. ``ensure_demo_dataset()`` is additive -- it creates missing
tables and inserts rows only when a table is empty, so pointing it at a
database that already holds this data (or real data) changes nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

TABLES: dict[str, str] = {
    "customers": """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            postal_code TEXT,
            registration_date TEXT
        )
    """,
    "products": """
        CREATE TABLE IF NOT EXISTS products (
            product_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            category TEXT,
            price REAL NOT NULL,
            cost REAL,
            stock_quantity INTEGER DEFAULT 0
        )
    """,
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            order_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            total_amount REAL,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        )
    """,
    "order_items": """
        CREATE TABLE IF NOT EXISTS order_items (
            item_id INTEGER PRIMARY KEY,
            order_id INTEGER,
            product_id INTEGER,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (order_id),
            FOREIGN KEY (product_id) REFERENCES products (product_id)
        )
    """,
    "sales_metrics": """
        CREATE TABLE IF NOT EXISTS sales_metrics (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            channel TEXT,
            revenue REAL,
            cost REAL,
            profit REAL
        )
    """,
}

ROWS: dict[str, list[tuple]] = {
    "customers": [
        (1, 'John', 'Doe', 'john.doe@example.com', '123-456-7890', '123 Main St', 'New York', 'NY', 'USA', '10001', '2023-01-15'),
        (2, 'Jane', 'Smith', 'jane.smith@example.com', '987-654-3210', '456 Oak Ave', 'Los Angeles', 'CA', 'USA', '90001', '2023-02-20'),
        (3, 'Alice', 'Johnson', 'alice@example.com', '555-123-4567', '789 Pine St', 'Chicago', 'IL', 'USA', '60007', '2023-03-10'),
        (4, 'Bob', 'Williams', 'bob@example.com', '555-987-6543', '101 Maple Dr', 'Houston', 'TX', 'USA', '77002', '2023-01-05'),
        (5, 'Emily', 'Brown', 'emily@example.com', '555-765-4321', '202 Cedar Ln', 'Miami', 'FL', 'USA', '33101', '2023-02-28'),
    ],
    "products": [
        (1, 'Laptop', 'High-performance laptop', 'Electronics', 1299.99, 900.00, 50),
        (2, 'Smartphone', '5G smartphone with high-res camera', 'Electronics', 799.99, 500.00, 100),
        (3, 'Coffee Maker', 'Automatic drip coffee maker', 'Home Appliances', 89.99, 45.00, 30),
        (4, 'Running Shoes', 'Comfortable athletic shoes', 'Apparel', 129.99, 60.00, 80),
        (5, 'Desk Chair', 'Ergonomic office chair', 'Furniture', 249.99, 120.00, 25),
        (6, 'Headphones', 'Noise-cancelling wireless headphones', 'Electronics', 199.99, 100.00, 60),
        (7, 'Blender', 'High-speed countertop blender', 'Home Appliances', 79.99, 40.00, 20),
        (8, 'Backpack', 'Water-resistant laptop backpack', 'Accessories', 59.99, 25.00, 45),
    ],
    "orders": [
        (1, 1, '2023-04-01', 'completed', 1299.99),
        (2, 2, '2023-04-05', 'completed', 879.98),
        (3, 3, '2023-04-10', 'completed', 89.99),
        (4, 4, '2023-04-15', 'shipped', 259.98),
        (5, 5, '2023-04-20', 'processing', 249.99),
        (6, 1, '2023-05-02', 'completed', 259.98),
        (7, 2, '2023-05-08', 'shipped', 199.99),
        (8, 3, '2023-05-15', 'processing', 139.98),
    ],
    "order_items": [
        (1, 1, 1, 1, 1299.99),
        (2, 2, 2, 1, 799.99),
        (3, 2, 3, 1, 79.99),
        (4, 3, 3, 1, 89.99),
        (5, 4, 4, 2, 259.98),
        (6, 5, 5, 1, 249.99),
        (7, 6, 6, 1, 199.99),
        (8, 6, 8, 1, 59.99),
        (9, 7, 6, 1, 199.99),
        (10, 8, 7, 1, 79.99),
        (11, 8, 8, 1, 59.99),
    ],
    "sales_metrics": [
        (1, '2023-01', 'Online', 15000.00, 8000.00, 7000.00),
        (2, '2023-02', 'Online', 18000.00, 9500.00, 8500.00),
        (3, '2023-03', 'Online', 22000.00, 11000.00, 11000.00),
        (4, '2023-04', 'Online', 24000.00, 12000.00, 12000.00),
        (5, '2023-05', 'Online', 26000.00, 13000.00, 13000.00),
        (6, '2023-01', 'Retail', 10000.00, 6000.00, 4000.00),
        (7, '2023-02', 'Retail', 12000.00, 7000.00, 5000.00),
        (8, '2023-03', 'Retail', 14000.00, 8000.00, 6000.00),
        (9, '2023-04', 'Retail', 15000.00, 8500.00, 6500.00),
        (10, '2023-05', 'Retail', 16000.00, 9000.00, 7000.00),
    ],
}


def row_counts(db_path: str | Path) -> dict[str, int]:
    """Row count per demo table (0 for a table that does not exist yet)."""
    conn = sqlite3.connect(str(db_path))
    try:
        counts: dict[str, int] = {}
        for name in TABLES:
            try:
                counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            except sqlite3.DatabaseError:
                counts[name] = 0
        return counts
    finally:
        conn.close()


def ensure_demo_dataset(db_path: str | Path) -> dict[str, int]:
    """Create missing demo tables and fill empty ones. Returns rows inserted."""
    inserted: dict[str, int] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        for name, ddl in TABLES.items():
            conn.execute(ddl)
        for name, rows in ROWS.items():
            existing = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            if existing:
                inserted[name] = 0
                continue
            placeholders = ", ".join("?" for _ in rows[0])
            conn.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)
            inserted[name] = len(rows)
        conn.commit()
    finally:
        conn.close()
    return inserted
