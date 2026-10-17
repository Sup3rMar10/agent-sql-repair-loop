"""A small synthetic receivables database (all values invented), rebuilt deterministically from a seed."""

from __future__ import annotations

import random
import sqlite3

REGIONS = ["north", "south", "east", "west"]
FIRST = ["Brindle", "Quillon", "Marlow", "Tarn", "Dunmore", "Verra", "Ellery", "Holloway", "Pennant", "Saxby"]
SECOND = ["Trading", "Supplies", "Logistics", "Holdings", "Foods", "Works"]

SCHEMA = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                       amount REAL NOT NULL, status TEXT NOT NULL, issued_on TEXT NOT NULL);
CREATE TABLE payments (id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
                       amount REAL NOT NULL, paid_on TEXT NOT NULL);
"""

# Foreign keys, as (child table, child column, parent table, parent column). Used for repair hints.
FOREIGN_KEYS = [("invoices", "customer_id", "customers", "id"), ("payments", "invoice_id", "invoices", "id")]
TABLES = {
    "customers": ["id", "name", "region"],
    "invoices": ["id", "customer_id", "amount", "status", "issued_on"],
    "payments": ["id", "invoice_id", "amount", "paid_on"],
}


def build_db(seed: int = 0, n_customers: int = 40, n_invoices: int = 300) -> sqlite3.Connection:
    rng = random.Random(seed)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(SCHEMA)
    for cid in range(1, n_customers + 1):
        conn.execute("INSERT INTO customers VALUES (?,?,?)",
                     (cid, f"{rng.choice(FIRST)} {rng.choice(SECOND)} {cid}", rng.choice(REGIONS)))
    pid = 1
    for iid in range(1, n_invoices + 1):
        amount = round(rng.uniform(50, 2000), 2)
        status = rng.choices(["paid", "open", "overdue"], weights=[5, 3, 2])[0]
        conn.execute("INSERT INTO invoices VALUES (?,?,?,?,?)",
                     (iid, rng.randint(1, n_customers), amount, status, f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"))
        if status == "paid":
            conn.execute("INSERT INTO payments VALUES (?,?,?,?)", (pid, iid, amount, "2026-10-01"))
            pid += 1
        elif status == "open" and rng.random() < 0.5:
            conn.execute("INSERT INTO payments VALUES (?,?,?,?)", (pid, iid, round(amount * rng.uniform(0.3, 0.7), 2), "2026-10-02"))
            pid += 1
    conn.commit()
    return conn


def fingerprint(conn: sqlite3.Connection) -> str:
    """A hash of every row of every table, to prove a run changed nothing."""
    import hashlib

    h = hashlib.sha256()
    for table in sorted(TABLES):
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):
            h.update(repr(row).encode())
    return h.hexdigest()
