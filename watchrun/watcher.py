"""The Watcher: ties scanning, debounce, running, and history together.

Poll loop, one iteration at a time:

1. :meth:`Watcher.poll_once` — build a fresh snapshot, diff it against
   the last one, and add any events to the pending batch. The time of
   the most recent change is recorded.
2. :meth:`Watcher.maybe_run` — if there are pending events and no new
   change has arrived for at least ``config.debounce`` seconds, run the
   command **once** for the whole batch (debounce coalescing) and write
   one history record. The trigger path/event reported are those of
   the *most recent* event in the batch.
3. Sleep ``config.poll_interval`` and repeat.

``clock`` and ``sleeper`` are injectable so tests can drive the debounce
logic with a fake clock instead of real sleeps. ``watch()`` accepts
``max_iterations`` — ``1`` powers the CLI's ``--once`` mode (a single
scan+run cycle; the first scan sees every file as ``added``).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable

from .config import Config
from .history import append_history
from .matcher import Matcher
from .runner import CommandResult, run_command
from .scanner import ChangeEvent, build_snapshot, detect_changes

__all__ = ["Watcher"]


class Watcher:
    """Poll-based watcher that runs a command on file changes."""

    def __init__(
        self,
        config: Config,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        verbose: bool = False,
    ) -> None:
        self.config = config
        self.matcher = Matcher(config.include, config.exclude)
        self.verbose = verbose
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self.snapshot: dict = {}
        self.pending: list[ChangeEvent] = []
        self._last_change_at: float | None = None
        # Never watch our own history file: appending a run record would
        # otherwise trigger another run, forever.
        self._skip_paths = {
            os.path.abspath(config.history_file).replace(os.sep, "/")
        }

    # -- logging ------------------------------------------------------
    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[watchrun] {message}", file=sys.stderr)

    # -- polling ------------------------------------------------------
    def poll_once(self) -> list[ChangeEvent]:
        """One scan cycle: detect changes, queue them, return them."""
        new_snapshot = build_snapshot(
            self.config.watch_paths,
            self.matcher,
            previous=self.snapshot,
            skip_paths=self._skip_paths,
        )
        events = detect_changes(self.snapshot, new_snapshot)
        self.snapshot = new_snapshot
        if events:
            self.pending.extend(events)
            self._last_change_at = self._clock()
            for event in events:
                self._log(f"{event.kind}: {event.path}")
        return events

    # -- running ------------------------------------------------------
    def _run_batch(self, batch: list[ChangeEvent]) -> CommandResult:
        """Run the command once for a batch of coalesced events."""
        trigger = batch[-1]  # most recent event drives {path}/{event}
        result = run_command(
            self.config.command,
            path=trigger.path,
            event=trigger.kind,
            timeout=self.config.timeout,
        )
        self._log(
            f"ran ({len(batch)} event(s), trigger {trigger.kind} "
            f"{trigger.path}): exit={result.exit_code} "
            f"in {result.duration:.2f}s"
            + (" [TIMED OUT]" if result.timed_out else "")
        )
        append_history(
            self.config.history_file,
            path=trigger.path,
            event=trigger.kind,
            command=result.command,
            exit_code=result.exit_code,
            duration=result.duration,
            stdout=result.stdout,
            stderr=result.stderr,
            events=len(batch),
            timed_out=result.timed_out,
        )
        return result

    def maybe_run(self) -> CommandResult | None:
        """Run once if pending events have been quiet for ``debounce``.

        Returns the result, or ``None`` when nothing is due yet.
        """
        if not self.pending or self._last_change_at is None:
            return None
        if self._clock() - self._last_change_at < self.config.debounce:
            return None
        batch = list(self.pending)
        self.pending.clear()
        return self._run_batch(batch)

    def run_once(self) -> CommandResult:
        """Run the command once immediately (manual trigger, no scan)."""
        trigger_path = self.config.watch_paths[0]
        result = run_command(
            self.config.command,
            path=trigger_path,
            event="manual",
            timeout=self.config.timeout,
        )
        append_history(
            self.config.history_file,
            path=trigger_path,
            event="manual",
            command=result.command,
            exit_code=result.exit_code,
            duration=result.duration,
            stdout=result.stdout,
            stderr=result.stderr,
            events=1,
            timed_out=result.timed_out,
        )
        return result

    def flush(self) -> CommandResult | None:
        """Run once for any pending events, ignoring the debounce wait."""
        if not self.pending:
            return None
        batch = list(self.pending)
        self.pending.clear()
        return self._run_batch(batch)

    # -- main loop ----------------------------------------------------
    def watch(self, max_iterations: int | None = None) -> None:
        """Poll forever (or for ``max_iterations`` cycles).

        On a clean loop exit (``--once``), leftover pending events are
        flushed so a single scan+run cycle actually runs. Ctrl-C stops
        the loop without flushing.
        """
        iterations = 0
        try:
            while max_iterations is None or iterations < max_iterations:
                self.poll_once()
                self.maybe_run()
                iterations += 1
                if max_iterations is None or iterations < max_iterations:
                    self._sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            self._log("stopped (Ctrl-C)")
            return
        self.flush()
