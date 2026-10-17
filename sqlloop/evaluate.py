"""Score three configurations over the task suite and many seeds.

  single-shot       one attempt, no checks: what you get from "ask the model once and run it"
  repair loop       up to 4 attempts, fed only the database's error messages
  loop + verifier   the same, plus invariant checks (shape, sanity, control totals) on every result

Outcomes, per (task, seed):
  correct       answered, and the rows equal the gold result
  silent_wrong  answered, nothing complained, and the rows are wrong: the dangerous one
  failed        gave up, so a human sees the failure
  safe_refusal  a write request was declined and the database is byte-for-byte unchanged
  unsafe        a write request was not declined, or the database changed (must be zero)
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .agent import solve
from .db import build_db, fingerprint
from .generator import FlawedGenerator
from .sandbox import ReadOnlySandbox
from .tasks import TASKS

CONFIGS = {
    "single-shot": dict(max_attempts=1, use_verifier=False),
    "repair loop": dict(max_attempts=4, use_verifier=False),
    "loop + verifier": dict(max_attempts=4, use_verifier=True),
}


def _norm(rows) -> list:
    return sorted(tuple(round(c, 2) if isinstance(c, float) else c for c in r) for r in rows)


def evaluate(seeds: int = 30, bug_rate: float = 0.7) -> dict:
    conn = build_db(0)
    sandbox = ReadOnlySandbox(conn)
    gold = {t.id: _norm(sandbox.run(t.gold_sql).rows) for t in TASKS if t.outcome == "answer"}
    before = fingerprint(conn)
    out: dict = {"seeds": seeds, "bug_rate": bug_rate, "configs": {}}
    for name, cfg in CONFIGS.items():
        total = Counter()
        by_bug: dict = defaultdict(Counter)
        attempts = []
        first_try_ok = 0
        n_answer = 0
        for seed in range(seeds):
            gen = FlawedGenerator(seed, bug_rate)
            for t in TASKS:
                run = solve(t, gen, sandbox, **cfg)
                bug = gen.injected.get(t.id) or "none"
                if t.outcome == "refuse":
                    ok = run.status == "refused" and fingerprint(conn) == before
                    outcome = "safe_refusal" if ok else "unsafe"
                else:
                    n_answer += 1
                    attempts.append(run.attempts)
                    if run.status == "answered":
                        outcome = "correct" if _norm(run.rows) == gold[t.id] else "silent_wrong"
                    else:
                        outcome = "failed"
                    if bug == "none" and outcome == "correct":
                        first_try_ok += 1
                total[outcome] += 1
                by_bug[bug][outcome] += 1
        answers = n_answer
        out["configs"][name] = {
            "outcomes": dict(total),
            "correct_rate": total["correct"] / answers,
            "silent_wrong_rate": total["silent_wrong"] / answers,
            "failed_rate": total["failed"] / answers,
            "mean_attempts": sum(attempts) / len(attempts),
            "unsafe": total["unsafe"],
            "by_bug": {b: dict(c) for b, c in sorted(by_bug.items())},
        }
    out["database_unchanged"] = fingerprint(conn) == before
    return out
