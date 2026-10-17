"""A stand-in for the model that writes SQL: it makes realistic, seeded mistakes and repairs some of them.

This is NOT a language model. It starts from the correct SQL, injects one of a few common bugs with a
seeded probability, and when given feedback it applies a repair if the feedback matches something it
knows how to fix. That makes the loop measurable and reproducible; it also means the repair rate here
describes the loop's plumbing (feedback reaches the generator, repairs are tried, unsafe SQL is stopped),
not how well a real model repairs SQL. The Claude generator in llm.py has the same interface.
"""

from __future__ import annotations

import difflib
import random
import re

from .db import FOREIGN_KEYS, TABLES
from .tasks import Task

COLUMN_TYPOS = {"amount": "amnt", "customer_id": "cust_id", "region": "regoin", "status": "stat", "name": "nme",
                "invoice_id": "invoice", "issued_on": "issued"}
ALL_COLUMNS = sorted({c for cols in TABLES.values() for c in cols})
_JOIN = re.compile(r"\bJOIN (\w+) ON .+?(?=\s+(?:LEFT JOIN|JOIN|WHERE|GROUP BY|ORDER BY|LIMIT)\b|$)", re.DOTALL)

BUG_TYPES = ["wrong_column", "wrong_table", "missing_join", "wrong_aggregate", "wrong_filter"]


def apply_bug(sql: str, bug: str) -> str | None:
    """Inject one bug, or return None if it does not apply to this query."""
    if bug == "wrong_column":
        for good, bad in COLUMN_TYPOS.items():
            if re.search(rf"\b{good}\b", sql):
                return re.sub(rf"\b{good}\b", bad, sql)
        return None
    if bug == "wrong_table":
        for t in TABLES:
            if re.search(rf"\b{t}\b", sql):
                return re.sub(rf"\b{t}\b", t[:-1], sql)
        return None
    if bug == "missing_join":
        # Only plain inner joins: dropping the ON clause from a LEFT JOIN changes meaning in a way the repair cannot know.
        for m in _JOIN.finditer(sql):
            if sql[max(0, m.start() - 5):m.start()] != "LEFT ":
                return sql[:m.start()] + f"CROSS JOIN {m.group(1)}" + sql[m.end():]
        return None
    if bug == "wrong_aggregate":
        return sql.replace("SUM(", "AVG(", 1) if "SUM(" in sql else None
    if bug == "wrong_filter":
        for good, bad in (("'overdue'", "'late'"), ("'north'", "'northern'")):
            if good in sql:
                return sql.replace(good, bad)
        return None
    raise ValueError(bug)


def _closest(word: str, options: list[str]) -> str | None:
    m = difflib.get_close_matches(word, options, n=1, cutoff=0.5)
    return m[0] if m else None


def _fk_condition(new_table: str, sql: str) -> str | None:
    """A join condition between `new_table` and any table already in the query, from the foreign keys."""
    present = [t for t in TABLES if re.search(rf"\b{t}\b", sql) and t != new_table]
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        if child == new_table and parent in present:
            return f"{child}.{ccol} = {parent}.{pcol}"
        if parent == new_table and child in present:
            return f"{child}.{ccol} = {parent}.{pcol}"
    return None


class FlawedGenerator:
    def __init__(self, seed: int = 0, bug_rate: float = 0.7):
        self.seed = seed
        self.bug_rate = bug_rate
        self.injected: dict[str, str | None] = {}  # task id -> the bug put in the first attempt (for reporting only)

    def first(self, task: Task) -> str:
        if task.outcome == "refuse":
            self.injected[task.id] = "write_request"
            return task.malicious_sql
        rng = random.Random(f"{self.seed}:{task.id}")
        if rng.random() >= self.bug_rate:
            self.injected[task.id] = None
            return task.gold_sql
        options = [(b, apply_bug(task.gold_sql, b)) for b in BUG_TYPES]
        options = [(b, s) for b, s in options if s and s != task.gold_sql]
        bug, sql = rng.choice(options)
        self.injected[task.id] = bug
        return sql

    def repair(self, task: Task, sql: str, kind: str, message: str) -> str | None:
        """The next attempt given feedback. None means 'I will not do this' (the right answer to a write request)."""
        if kind in ("forbidden", "multiple_statements"):
            return None
        if kind == "no_such_column":
            m = re.search(r"no such column: (?:(\w+)\.)?(\w+)", message)
            if m:
                fix = _closest(m.group(2), ALL_COLUMNS)
                if fix:
                    return re.sub(rf"\b{m.group(2)}\b", fix, sql)
        if kind == "no_such_table":
            m = re.search(r"no such table: (\w+)", message)
            if m:
                fix = _closest(m.group(1), list(TABLES))
                if fix:
                    return re.sub(rf"\b{m.group(1)}\b", fix, sql)
        if "CROSS JOIN" in sql:
            m = re.search(r"CROSS JOIN (\w+)", sql)
            # Only tables already joined in the FROM clause (not the SELECT list) can appear in its ON clause.
            cond = _fk_condition(m.group(1), sql[sql.find("FROM"):m.start()]) if m else None
            if cond:
                return sql.replace(m.group(0), f"JOIN {m.group(1)} ON {cond}", 1)
        return sql  # nothing recognisable to fix: the same SQL again, which the loop treats as no progress
