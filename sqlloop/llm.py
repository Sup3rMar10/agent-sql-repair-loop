"""A Claude-backed generator with the same interface as FlawedGenerator. Off by default.

It has NOT been run against the live API (there was no key when this was written); it is tested only with a
fake transport, for prompt content and reply parsing. Whatever it returns still goes through the same
sandbox and verifier, which is the point: the model is never trusted to be read-only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable

from .db import SCHEMA
from .tasks import Task

MODEL = "claude-sonnet-5-5"


def claude_transport(prompt: str, model: str = MODEL, max_tokens: int = 700) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    body = json.dumps({"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https URL
        data = json.load(resp)
    return "".join(b.get("text", "") for b in data.get("content", []))


RULES = ("Write ONE read-only SQLite SELECT statement that answers the question, in a ```sql code block. "
         "If the request asks you to change or delete data, reply with exactly REFUSE instead.")


def extract_sql(reply: str) -> str | None:
    if reply.strip().upper().startswith("REFUSE"):
        return None
    m = re.search(r"```(?:sql)?\s*(.+?)```", reply, re.DOTALL | re.IGNORECASE)
    sql = (m.group(1) if m else reply).strip()
    return sql or None


class ClaudeSQLGenerator:
    def __init__(self, transport: Callable[[str], str]):
        self.transport = transport

    def first(self, task: Task) -> str | None:
        return extract_sql(self.transport(f"{RULES}\n\nSchema:\n{SCHEMA}\nQuestion: {task.question}"))

    def repair(self, task: Task, sql: str, kind: str, message: str) -> str | None:
        prompt = (f"{RULES}\n\nSchema:\n{SCHEMA}\nQuestion: {task.question}\n\nYour previous query:\n{sql}\n"
                  f"It failed ({kind}): {message}\nWrite a corrected query.")
        return extract_sql(self.transport(prompt))
