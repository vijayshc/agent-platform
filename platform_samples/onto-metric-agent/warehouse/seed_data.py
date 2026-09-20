"""Deterministic bank dataset used to populate the DuckDB warehouse.

Numbers are chosen so the evaluation answers are exact. This is the only place
the sample's data is defined.
"""

from __future__ import annotations

# legal_entity_id, name, parent_id
LEGAL_ENTITIES = [
    ("LE1", "UOB Holdings", None),
    ("LE2", "UOB Private Bank", "LE1"),
    ("LE3", "UOB Securities", "LE1"),
]

# customer_id, name, segment, legal_entity_id, cif_no
CUSTOMERS = [
    ("C1", "Alice Tan", "PB", "LE2", "CIF001"),
    ("C2", "Bob Lim", "PB", "LE2", "CIF002"),
    ("C3", "Carol Ong", "Retail", "LE3", "CIF003"),
    ("C4", "Dan Wong", "PB", "LE1", "CIF004"),
]

# account_id, customer_id, status, book, open_date
ACCOUNTS = [
    ("A1", "C1", "Active", "SG", "2022-01-10"),
    ("A2", "C1", "Active", "SG", "2023-03-01"),
    ("A3", "C2", "Active", "SG", "2021-07-15"),
    ("A4", "C3", "Active", "SG", "2024-02-20"),
    ("A5", "C4", "Active", "HK", "2020-11-05"),
]

# product_id, name, parent_product_id, asset_class
PRODUCTS = [
    ("P_CASH", "Cash", None, "Cash"),
    ("P_EQ", "Listed Equity", None, "Equity"),
    ("P_SN", "Structured Notes", None, "Structured"),
    ("P_SN_ELN", "Equity Linked Note", "P_SN", "Structured"),
    ("P_SN_FCN", "Fixed Coupon Note", "P_SN", "Structured"),
]

# account_id, product_id, as_of_date, market_value, currency
HOLDINGS = [
    # 2026-06-30 snapshot
    ("A1", "P_CASH", "2026-06-30", 100_000, "SGD"),
    ("A1", "P_SN_ELN", "2026-06-30", 400_000, "SGD"),
    ("A2", "P_EQ", "2026-06-30", 250_000, "SGD"),
    ("A3", "P_SN_FCN", "2026-06-30", 300_000, "SGD"),
    ("A3", "P_CASH", "2026-06-30", 50_000, "SGD"),
    ("A4", "P_EQ", "2026-06-30", 80_000, "SGD"),
    ("A4", "P_SN", "2026-06-30", 20_000, "SGD"),
    ("A5", "P_SN_ELN", "2026-06-30", 1_000_000, "SGD"),
    # 2026-03-31 snapshot
    ("A1", "P_CASH", "2026-03-31", 90_000, "SGD"),
    ("A1", "P_SN_ELN", "2026-03-31", 350_000, "SGD"),
    ("A2", "P_EQ", "2026-03-31", 200_000, "SGD"),
    ("A3", "P_SN_FCN", "2026-03-31", 280_000, "SGD"),
    ("A5", "P_SN_ELN", "2026-03-31", 900_000, "SGD"),
]

# control_id, name
CONTROLS = [("CTL_SUIT", "Suitability")]

# account_id, control_id, event_date, result
BREACHES = [
    ("A1", "CTL_SUIT", "2026-05-10", "FAIL"),  # Q2
    ("A4", "CTL_SUIT", "2026-06-01", "FAIL"),  # Q2
    ("A3", "CTL_SUIT", "2026-04-12", "PASS"),  # Q2 pass — not a fail
    ("A5", "CTL_SUIT", "2026-01-15", "FAIL"),  # Q1 — outside the window
]
