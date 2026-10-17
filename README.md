# agent-sql-repair-loop

![ci](https://github.com/Sup3rMar10/agent-sql-repair-loop/actions/workflows/ci.yml/badge.svg)

A text-to-SQL agent built around the parts that decide whether you can trust it: a **read-only sandbox enforced by the database itself**, **invariant checks** (including control totals) that catch wrong answers the database never complains about, and a **repair loop** that feeds failures back and knows when to stop.

> **What is verified, and what is not.** 71 tests pass locally on Python 3.13, and in GitHub Actions on Linux with Python 3.11 and 3.13 (stdlib only; SQLite). Deleting the authorizer makes 21 of them fail, and disabling the control-total check makes 5 fail. **There is no live model.** The SQL generator is a rule-based stand-in that injects realistic bugs and repairs some of them, so the repair rates below describe the loop's machinery, not how well a real model fixes SQL. The Claude generator (`sqlloop/llm.py`) is tested only with a fake transport, never against the live API.

## The sandbox (`sqlloop/sandbox.py`)

Model-written SQL is untrusted. The defences are enforced by SQLite while it compiles and runs the statement, **not** by scanning the query text (which `/**/DROP` and `WITH ... DELETE` defeat):

| Defence | What it stops |
|---|---|
| **Authorizer callback**: only SELECT, READ and a few functions are allowed | INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, PRAGMA, ATTACH, VACUUM, triggers, `load_extension`, however spelled or nested |
| One statement per call (checked ignoring string literals) | `SELECT 1; DROP TABLE payments` |
| **Progress handler**: abort after a step budget | endless recursion, accidental cartesian products |
| Row cap | an unbounded result flooding the model's context |

`python -m sqlloop attack` runs 14 attacks: all blocked, database unchanged (verified by hashing every row before and after). Errors come back typed (`no_such_column`, `forbidden`, `timeout`, ...) with messages written for the next attempt to act on.

## The loop (`sqlloop/agent.py`)

Generate, run in the sandbox, verify, and on failure feed the message back: up to 4 attempts. It stops early when the generator **declines** (the right answer to "delete all the paid invoices") or **proposes the same query again** (no progress), rather than burning attempts.

**Invariants** (`sqlloop/verify.py`) are what an analyst checks by eye, and need no gold answer: the shape (column count, row bounds), sane amounts (non-negative), a minimum count where zero would be absurd, and **control totals**: the column of per-customer totals must add up to `SELECT SUM(amount) FROM invoices`. A missing join or a wrong aggregate breaks that, even though the database returns rows happily.

## Measured (`python -m sqlloop eval`; 10 questions + 2 write requests, 30 seeds, first attempt buggy with probability 0.7)

| Configuration | Correct | Silent wrong | Failed (visible) | Mean attempts | Unsafe |
|---|---|---|---|---|---|
| single-shot, no checks | 33.3% | 14.7% | 52.0% | 1.00 | 0 |
| repair loop (database errors only) | 85.3% | **14.7%** | 0.0% | 1.52 | 0 |
| repair loop + verifier | 86.3% | **6.0%** | 7.7% | 1.53 | 0 |

The write requests were declined, with the database unchanged, **60 of 60 times**.

### What this says, and what it does not

- **The loop is worth most for errors the database explains** (misspelled columns and tables: 148 of 148 repaired).
- **The verifier barely raises the correct rate (85.3% to 86.3%). What it does is convert silent wrong answers into visible failures** (14.7% down to 6.0%, with 7.7% now reported as failed). A failure a person sees is far better than a wrong number nobody questions. That is the real value of checking results, and it costs almost nothing in attempts.
- **6.0% of answers are still silently wrong, and that is the honest limit.** By injected bug, with the verifier on:

| Injected bug | n | Correct | Silent wrong | Failed (caught) |
|---|---|---|---|---|
| none | 100 | 100 | 0 | 0 |
| wrong column / wrong table | 69 / 79 | all | 0 | 0 |
| missing join | 14 | 11 | 3 | 0 |
| wrong filter value (`'overdue'` to `'late'`) | 11 | 0 | 0 | 11 |
| wrong aggregate (`SUM` to `AVG`) | 27 | 0 | **15** | 12 |

  The 15 silent wrong aggregates are `paid_total` (9) and `top5_customers` (6) with `AVG` for `SUM`; the 3 silent missing joins are `top5_customers` again with a cartesian product. In each case the result has the right shape, positive amounts and no control total to compare against, so nothing objects. The 12 caught aggregates are `invoiced_by_customer` (8) and `overdue_amount_by_region` (4), whose control totals break. No generic check finds every wrong answer; each task needs invariants someone thought of.

## Honest limits

- **The stand-in is circular.** I wrote both the bug injector and the repair rules, so it repairs the bugs I thought of, by construction. The numbers show the harness works and where the checks have gaps, not how a real model performs. Bug counts per type are uneven (they depend on which tasks a bug applies to).
- **Two bugs in my own stand-in, found by reading failures:** its foreign-key repair first picked a join condition that referenced a table appearing later in the query, and then one that was fooled by a table name in the `SELECT` list. Both were repair-heuristic bugs, not loop findings. I fixed them before reporting, and the numbers above are after the fixes.
- **The sandbox protects integrity, not confidentiality.** A read-only query can still read every table, including `sqlite_master`, and show it to the model. Production needs a database role limited to the tables the agent may see, and row-level rules, in addition to this.
- The step budget counts SQLite VM steps, not wall-clock time, and the row cap is applied as results are fetched.
- This is SQLite's authorizer; other databases need their own mechanism (a read-only role, `SET TRANSACTION READ ONLY`, a proxy that parses SQL).
- Invariants are hand-written per task. That does not scale to arbitrary questions; it is a pattern for the reports an agent answers repeatedly.
- Correctness is scored by comparing rows to a gold query (order-insensitive, floats rounded to 2 places).

## Run it

```
python -m pytest
python -m sqlloop attack
python -m sqlloop demo
python -m sqlloop eval --seeds 30 --out results/eval.json
```

Python 3.11 or later; SQLite ships with Python. To use a real model, give `ClaudeSQLGenerator` a prompt-to-text function (`claude_transport` reads `ANTHROPIC_API_KEY`); everything it writes still passes through the same sandbox and checks.
