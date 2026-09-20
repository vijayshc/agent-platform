#!/usr/bin/env python3
"""Load the raw bank tables into the DuckDB warehouse.

Only raw, source-shaped tables are written here. All analytics shaping — the
product-family rollup, the legal-entity closure, the control-event fact — is done
by dbt models, so the warehouse has one transformation layer, not two.

    python warehouse/build.py            # writes warehouse/bank.duckdb
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from seed_data import ACCOUNTS, BREACHES, CONTROLS, CUSTOMERS, HOLDINGS, LEGAL_ENTITIES, PRODUCTS

DB_PATH = Path(__file__).resolve().parent / "bank.duckdb"

SCHEMA = """
CREATE OR REPLACE TABLE legal_entity (
    legal_entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT
);
CREATE OR REPLACE TABLE customer (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL,
    legal_entity_id TEXT NOT NULL,
    cif_no TEXT NOT NULL
);
CREATE OR REPLACE TABLE account (
    account_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    book TEXT NOT NULL,
    open_date DATE NOT NULL
);
CREATE OR REPLACE TABLE product (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_product_id TEXT,
    asset_class TEXT NOT NULL
);
CREATE OR REPLACE TABLE holding (
    holding_id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    market_value DOUBLE NOT NULL,
    currency TEXT NOT NULL
);
CREATE OR REPLACE TABLE control_def (
    control_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE OR REPLACE TABLE control_breach (
    breach_id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    event_date DATE NOT NULL,
    result TEXT NOT NULL
);
"""


def build(db_path: Path = DB_PATH) -> Path:
    con = duckdb.connect(str(db_path))
    try:
        con.execute(SCHEMA)
        con.executemany("INSERT INTO legal_entity VALUES (?, ?, ?)", LEGAL_ENTITIES)
        con.executemany("INSERT INTO customer VALUES (?, ?, ?, ?, ?)", CUSTOMERS)
        con.executemany("INSERT INTO account VALUES (?, ?, ?, ?, ?)", ACCOUNTS)
        con.executemany("INSERT INTO product VALUES (?, ?, ?, ?)", PRODUCTS)
        con.executemany(
            "INSERT INTO holding VALUES (?, ?, ?, ?, ?, ?)",
            [(i + 1, *row) for i, row in enumerate(HOLDINGS)],
        )
        con.executemany("INSERT INTO control_def VALUES (?, ?)", CONTROLS)
        con.executemany(
            "INSERT INTO control_breach VALUES (?, ?, ?, ?, ?)",
            [(i + 1, *row) for i, row in enumerate(BREACHES)],
        )
    finally:
        con.close()
    return db_path


if __name__ == "__main__":
    print(f"Wrote {build()}")
