"""Command execution for watchrun: templating, timeout, output capture.

The configured command is a shell string run with ``shell=True``. Two
placeholders are substituted before the shell sees it:

- ``{path}``  — absolute path of the file that triggered the run
- ``{event}`` — the trigger event type: ``added`` / ``modified`` /
  ``deleted`` / ``manual``

On timeout the process is killed (``subprocess`` timeout handling) and
the result reports ``exit_code == -1`` and ``timed_out == True`` along
with whatever output was captured before the kill. Note: only the direct
child process is killed; a command that spawns detached grandchildren
may leave them running — that is a documented limitation.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

__all__ = ["CommandResult", "run_command", "substitute"]

TIMEOUT_EXIT_CODE = -1


@dataclass
class CommandResult:
    command: str  # the command after placeholder substitution
    exit_code: int  # -1 when the command timed out
    stdout: str
    stderr: str
    duration: float  # seconds, wall clock
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


def substitute(command: str, path: str = "", event: str = "manual") -> str:
    """Replace ``{path}`` and ``{event}`` placeholders in ``command``."""
    return command.replace("{path}", path).replace("{event}", event)


def run_command(
    command: str,
    *,
    path: str = "",
    event: str = "manual",
    timeout: float = 30.0,
    cwd: str | None = None,
) -> CommandResult:
    """Run ``command`` once; capture stdout/stderr; enforce ``timeout``."""
    rendered = substitute(command, path=path, event=event)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            rendered,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
        return CommandResult(
            command=rendered,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration=time.monotonic() - started,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=rendered,
            exit_code=TIMEOUT_EXIT_CODE,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration=time.monotonic() - started,
            timed_out=True,
        )
