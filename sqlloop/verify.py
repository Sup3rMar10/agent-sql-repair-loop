"""Invariant checks on a result set, written so the failure message tells the next attempt what to fix.

None of these look at the gold answer. They are the checks a careful analyst applies by eye: does it have
the shape I expected, are the amounts sane, and does it add up to a number I can get another way (a
control total). They catch some wrong answers and miss others, and the evaluation reports both.
"""

from __future__ import annotations

from .sandbox import QueryResult, ReadOnlySandbox, SqlError
from .tasks import Expect


def verify(expect: Expect, result: QueryResult, sandbox: ReadOnlySandbox) -> list[str]:
    problems: list[str] = []
    rows = result.rows
    if expect.columns is not None and len(result.columns) != expect.columns:
        problems.append(f"expected {expect.columns} column(s) but the query returns {len(result.columns)}")
    if expect.min_rows is not None and len(rows) < expect.min_rows:
        problems.append(f"returned {len(rows)} row(s) but at least {expect.min_rows} are expected; check the filter values and join type")
    if expect.max_rows is not None and len(rows) > expect.max_rows:
        problems.append(f"row count {len(rows)} exceeds the expected maximum {expect.max_rows}: likely a missing join condition")
    for col in expect.nonneg:
        if any(isinstance(r[col], (int, float)) and r[col] < 0 for r in rows if len(r) > col):
            problems.append(f"column {col + 1} contains a negative amount")
    if expect.min_cell is not None and rows:
        col, floor = expect.min_cell
        if len(rows[0]) > col and isinstance(rows[0][col], (int, float)) and rows[0][col] < floor:
            problems.append(f"the value in column {col + 1} is {rows[0][col]} but at least {floor} is expected; check the filter values")
    if expect.control_total is not None and rows and not problems:
        col, control_sql = expect.control_total
        try:
            control = sandbox.run(control_sql).rows[0][0]
        except SqlError:
            control = None
        if control is not None and len(rows[0]) > col:
            total = sum(r[col] for r in rows if isinstance(r[col], (int, float)))
            if abs(total - control) > 0.5:
                problems.append(f"column {col + 1} adds up to {total:.2f} but the control total is {control:.2f}: "
                                "rows are duplicated, or an aggregate or filter is wrong")
    return problems
