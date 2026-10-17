"""Run model-written SQL so that it cannot change anything or run away.

The defences are enforced by SQLite itself, not by inspecting the text of the query (text checks are
the kind of thing `/**/DROP` and `WITH ... DELETE` get around):

  - an *authorizer* callback that SQLite calls for every operation while compiling the statement:
    only SELECT, READ and a short list of functions are allowed, so INSERT, UPDATE, DELETE, DROP,
    CREATE, ALTER, PRAGMA, ATTACH and friends are denied however they are spelled or nested;
  - the sqlite3 module accepts one statement per call, and a second statement is rejected up front;
  - a *progress handler* that aborts the query after a fixed number of VM steps (a runaway recursive
    query or an accidental cartesian product cannot hang the agent);
  - a hard cap on rows returned.

Errors come back as a typed SqlError whose message is written for the model to act on.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
DENIED_FUNCTIONS = {"load_extension"}


class SqlError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # syntax | no_such_column | no_such_table | forbidden | timeout | too_many_rows | multiple_statements | other
        self.message = message


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]


class ReadOnlySandbox:
    def __init__(self, conn: sqlite3.Connection, max_rows: int = 1000, max_steps: int = 400_000):
        self.conn = conn
        self.max_rows = max_rows
        self.max_steps = max_steps

    @staticmethod
    def _authorize(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in ALLOWED_ACTIONS else sqlite3.SQLITE_DENY

    def run(self, sql: str) -> QueryResult:
        text = sql.strip().rstrip(";").strip()
        if not text:
            raise SqlError("syntax", "the query is empty")
        if ";" in _strip_literals(text):
            raise SqlError("multiple_statements", "only a single SELECT statement is allowed")
        steps = {"n": 0}

        def progress() -> int:
            steps["n"] += 1000
            return 1 if steps["n"] > self.max_steps else 0

        self.conn.set_authorizer(self._authorize)
        self.conn.set_progress_handler(progress, 1000)
        try:
            cur = self.conn.execute(text)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.DatabaseError as e:
            raise _classify(e) from None
        except sqlite3.Warning as e:  # "You can only execute one statement at a time"
            raise SqlError("multiple_statements", str(e)) from None
        finally:
            self.conn.set_authorizer(None)
            self.conn.set_progress_handler(None, 0)
        if len(rows) > self.max_rows:
            raise SqlError("too_many_rows", f"the query returns more than {self.max_rows} rows; add a filter, an aggregate or a LIMIT")
        return QueryResult(cols, rows)


def _strip_literals(text: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", text)


def _classify(e: sqlite3.DatabaseError) -> SqlError:
    msg = str(e)
    low = msg.lower()
    if "not authorized" in low or "authoriz" in low:
        return SqlError("forbidden", "not authorized: only read-only SELECT queries are allowed")
    if "interrupted" in low:
        return SqlError("timeout", "the query ran too long and was stopped; check for a missing join condition or an unbounded recursion")
    if "no such column" in low:
        return SqlError("no_such_column", msg)
    if "no such table" in low:
        return SqlError("no_such_table", msg)
    if "syntax error" in low or "incomplete input" in low:
        return SqlError("syntax", msg)
    return SqlError("other", msg)
