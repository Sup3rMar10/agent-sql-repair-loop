"""The loop: generate, run in the sandbox, verify against invariants, feed failures back, stop when it should."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .sandbox import ReadOnlySandbox, SqlError
from .tasks import Task
from .verify import verify


class Generator(Protocol):
    def first(self, task: Task) -> str | None: ...
    def repair(self, task: Task, sql: str, kind: str, message: str) -> str | None: ...


@dataclass
class Run:
    status: str  # answered | refused | failed
    attempts: int
    sql: str | None
    rows: list = field(default_factory=list)
    feedback: list = field(default_factory=list)  # (attempt, kind, message) for every failed attempt
    reason: str = ""


def solve(task: Task, gen: Generator, sandbox: ReadOnlySandbox, max_attempts: int = 4, use_verifier: bool = True) -> Run:
    sql = gen.first(task)
    feedback: list = []
    for attempt in range(1, max_attempts + 1):
        if sql is None:
            return Run("refused", attempt - 1, None, feedback=feedback, reason="the generator declined")
        try:
            result = sandbox.run(sql)
        except SqlError as e:
            kind, message = e.kind, e.message
        else:
            problems = verify(task.expect, result, sandbox) if use_verifier else []
            if not problems:
                return Run("answered", attempt, sql, result.rows, feedback)
            kind, message = "invariant", "; ".join(problems)
        feedback.append((attempt, kind, message))
        nxt = gen.repair(task, sql, kind, message)
        if nxt is None:  # declining is a final answer, not a failure to find one
            return Run("refused", attempt, None, feedback=feedback, reason=f"declined after: {kind}")
        if nxt == sql:
            return Run("failed", attempt, sql, feedback=feedback, reason="no progress: the same query was proposed again")
        sql = nxt
    return Run("failed", max_attempts, sql, feedback=feedback, reason="out of attempts")
