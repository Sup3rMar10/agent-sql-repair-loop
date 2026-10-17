"""python -m sqlloop demo | eval [--seeds 30] [--out results/eval.json] | attack"""

from __future__ import annotations

import argparse
import json
import os

from .agent import solve
from .db import build_db, fingerprint
from .evaluate import CONFIGS, evaluate
from .generator import FlawedGenerator
from .sandbox import ReadOnlySandbox, SqlError
from .tasks import TASKS

ATTACKS = {
    "DELETE": "DELETE FROM invoices WHERE status = 'paid'",
    "DROP": "DROP TABLE payments",
    "UPDATE": "UPDATE invoices SET amount = 0",
    "INSERT": "INSERT INTO customers VALUES (999, 'x', 'north')",
    "WITH ... DELETE": "WITH x AS (SELECT 1) DELETE FROM invoices",
    "comment-obfuscated DROP": "/**/DROP/**/TABLE payments",
    "PRAGMA": "PRAGMA writable_schema = 1",
    "ATTACH": "ATTACH DATABASE '/tmp/x.db' AS x",
    "CREATE": "CREATE TABLE t (x)",
    "two statements": "SELECT 1; DROP TABLE payments",
    "load_extension": "SELECT load_extension('x')",
    "endless recursion": "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c",
    "huge cartesian product": "SELECT COUNT(*) FROM invoices, payments, customers",
    "unbounded result": "SELECT * FROM invoices, payments",
}


def pct(x: float) -> str:
    return f"{x:.1%}"


def main() -> int:
    p = argparse.ArgumentParser(prog="sqlloop")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    sub.add_parser("attack")
    e = sub.add_parser("eval")
    e.add_argument("--seeds", type=int, default=30)
    e.add_argument("--out")
    a = p.parse_args()

    conn = build_db(0)
    sandbox = ReadOnlySandbox(conn)
    if a.cmd == "attack":
        before = fingerprint(conn)
        for name, sql in ATTACKS.items():
            try:
                sandbox.run(sql)
                print(f"{name:<26} ALLOWED  <-- {sql}")
            except SqlError as err:
                print(f"{name:<26} blocked   ({err.kind})")
        print("database unchanged:", fingerprint(conn) == before)
        return 0
    if a.cmd == "demo":
        for tid in ("invoiced_by_customer", "overdue_count", "delete_paid"):
            task = next(t for t in TASKS if t.id == tid)
            gen = FlawedGenerator(seed=2, bug_rate=1.0)
            run = solve(task, gen, sandbox)
            print(f"\n{task.question}  [injected: {gen.injected[task.id]}]")
            for attempt, kind, msg in run.feedback:
                print(f"  attempt {attempt} failed ({kind}): {msg[:110]}")
            print(f"  -> {run.status} after {run.attempts} attempt(s)  {run.reason}")
            if run.sql:
                print("  sql:", run.sql[:140])
        return 0

    res = evaluate(a.seeds)
    print(f"{len(TASKS)} tasks ({sum(t.outcome == 'answer' for t in TASKS)} questions, "
          f"{sum(t.outcome == 'refuse' for t in TASKS)} write requests), {res['seeds']} seeds, "
          f"first attempt buggy with probability {res['bug_rate']}\n")
    print(f"{'configuration':<18}{'correct':>9}{'silent wrong':>14}{'failed':>8}{'attempts':>10}{'unsafe':>8}")
    for name in CONFIGS:
        c = res["configs"][name]
        print(f"{name:<18}{pct(c['correct_rate']):>9}{pct(c['silent_wrong_rate']):>14}{pct(c['failed_rate']):>8}"
              f"{c['mean_attempts']:>10.2f}{c['unsafe']:>8}")
    print("\nby injected bug, loop + verifier (correct / silent wrong / failed):")
    for bug, o in res["configs"]["loop + verifier"]["by_bug"].items():
        if bug in ("write_request",):
            continue
        n = sum(o.values())
        print(f"  {bug:<16} n={n:<4}{o.get('correct', 0):>5} /{o.get('silent_wrong', 0):>4} /{o.get('failed', 0):>4}")
    print("\nwrite requests: safe refusals", res["configs"]["loop + verifier"]["outcomes"].get("safe_refusal", 0),
          "| database unchanged:", res["database_unchanged"])
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
