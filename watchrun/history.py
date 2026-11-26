"""Run history log for watchrun: one JSON object per line.

Each record has: ``timestamp`` (UTC, ISO 8601), ``path`` (trigger path),
``event`` (added/modified/deleted/manual), ``command`` (as executed),
``exit_code`` (``-1`` on timeout), ``timed_out``, ``duration`` (seconds),
``events`` (how many change events were coalesced into this run) and
``output_tail`` (the last lines of combined stdout+stderr, so the log
stays small even for chatty commands).

The history file is append-only. Corrupt or blank lines are skipped
silently when reading, so one bad line never breaks the whole log.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

__all__ = ["append_history", "read_history", "tail_lines"]

DEFAULT_TAIL_LINES = 20


def tail_lines(text: str, max_lines: int = DEFAULT_TAIL_LINES) -> str:
    """Return the last ``max_lines`` lines of ``text``."""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:]) if lines else ""


def append_history(
    history_file: str,
    *,
    path: str,
    event: str,
    command: str,
    exit_code: int,
    duration: float,
    stdout: str = "",
    stderr: str = "",
    events: int = 1,
    timed_out: bool = False,
) -> dict:
    """Append one run record to ``history_file``; returns the record."""
    combined = (stdout + ("\n" + stderr if stderr else "")).strip("\n")
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": path,
        "event": event,
        "command": command,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration": round(duration, 3),
        "events": events,
        "output_tail": tail_lines(combined),
    }
    parent = os.path.dirname(os.path.abspath(history_file))
    os.makedirs(parent, exist_ok=True)
    with open(history_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_history(history_file: str) -> list[dict]:
    """Read all valid records from ``history_file`` (oldest first)."""
    if not os.path.exists(history_file):
        return []
    records: list[dict] = []
    with open(history_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip one corrupt line, keep the rest
            if isinstance(record, dict):
                records.append(record)
    return records
