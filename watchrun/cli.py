"""Command-line interface for watchrun.

Subcommands:

- ``watchrun init [--path PATH] [--force]`` — scaffold a starter
  ``watchrun.json`` (defaults) in the current directory.
- ``watchrun watch [--config PATH] [--once] [--verbose]`` — poll the
  configured paths and run the command on change. ``--once`` performs a
  single scan+run cycle and exits (useful for smoke tests).
- ``watchrun run-once [--config PATH]`` — run the configured command a
  single time immediately, without scanning (manual trigger).

Exit codes:

- ``0`` — success (watch stopped cleanly, init wrote the file,
  run-once command exited 0)
- ``1`` — config error (missing/malformed/invalid config), init target
  already exists, or a run-once command that failed without its own
  exit code (e.g. killed by timeout — timeout itself reports ``124``)
- ``2`` — argument error (argparse default)
- ``124`` — run-once command timed out
- otherwise, ``run-once`` exits with the command's own exit code
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import (
    ConfigError,
    load_config,
    write_init_config,
)
from .watcher import Watcher

DEFAULT_CONFIG_PATH = "watchrun.json"
TIMEOUT_EXIT_CODE = 124


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchrun",
        description=(
            "Poll-based file-watch command runner: "
            "run a shell command whenever watched files change."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_init = sub.add_parser(
        "init", help="scaffold a starter watchrun.json config file"
    )
    p_init.add_argument(
        "--path",
        default=DEFAULT_CONFIG_PATH,
        help="where to write the config (default: watchrun.json)",
    )
    p_init.add_argument(
        "--force", action="store_true", help="overwrite an existing file"
    )

    p_watch = sub.add_parser(
        "watch", help="watch for changes and run the command"
    )
    p_watch.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="config file to load (default: watchrun.json)",
    )
    p_watch.add_argument(
        "--once",
        action="store_true",
        help="perform a single scan+run cycle, then exit",
    )
    p_watch.add_argument(
        "--verbose", action="store_true", help="log scans, events, and runs"
    )

    p_once = sub.add_parser(
        "run-once", help="run the configured command once, immediately"
    )
    p_once.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="config file to load (default: watchrun.json)",
    )
    return parser


def _error(message: str) -> int:
    print(f"watchrun: error: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.subcommand == "init":
        try:
            path = write_init_config(args.path, overwrite=args.force)
        except ConfigError as exc:
            return _error(str(exc))
        print(f"wrote {path} — edit it, then run `watchrun watch`")
        return 0

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        return _error(str(exc))

    watcher = Watcher(config, verbose=getattr(args, "verbose", False))

    if args.subcommand == "watch":
        watcher.watch(max_iterations=1 if args.once else None)
        return 0

    if args.subcommand == "run-once":
        result = watcher.run_once()
        if result.timed_out:
            print(
                f"watchrun: command timed out after {config.timeout:g}s",
                file=sys.stderr,
            )
            return TIMEOUT_EXIT_CODE
        if result.exit_code != 0:
            print(
                f"watchrun: command exited with code {result.exit_code}",
                file=sys.stderr,
            )
            return result.exit_code
        return 0

    return 2  # pragma: no cover - argparse enforces a subcommand


if __name__ == "__main__":
    raise SystemExit(main())
