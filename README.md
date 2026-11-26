# watchrun

A small file-watch command runner built from scratch in pure Python
(standard library only — no third-party watcher, no `inotify`).

Watch files and directories; when something changes, run a shell
command. Useful for re-running tests on save, rebuilding docs, or
restarting a dev server.

```bash
python -m watchrun init      # scaffold watchrun.json
python -m watchrun watch     # watch and run on change (Ctrl-C to stop)
python -m watchrun run-once  # run the configured command once, now
```

Requires Python 3.10+ (developed and tested on 3.12). No dependencies
to install.

## How it works

**Polling, not events.** Every `poll_interval` seconds watchrun walks
the watch paths and builds a *snapshot*: for each file, its mtime,
size, and SHA-256 content hash. The new snapshot is diffed against the
previous one:

| Situation | Event |
| --- | --- |
| Path in new snapshot only | `added` |
| Path in old snapshot only | `deleted` |
| mtime/size changed *and* content hash differs | `modified` |
| mtime changed but content hash identical (a bare `touch`) | *no event* |

**Hashing stays cheap.** The SHA-256 is only computed when a file's
mtime *or* size changed since the last snapshot; otherwise the stored
hash is reused and the file is not re-read. That is the whole point of
keeping mtime + size in the snapshot: most polls touch no file bytes
at all.

**Debounce.** Rapid successive changes are coalesced into a single
run: after each detected change the quiet-period clock restarts, and
the command runs only once no new change has arrived for `debounce`
seconds. The run is triggered by the *most recent* event in the batch
(its path/event fill the `{path}` / `{event}` placeholders), and the
history record notes how many events were coalesced.

**Command execution.** The command is a shell string run with
`subprocess` (`shell=True`), with `{path}` and `{event}` substituted
beforehand. Stdout and stderr are captured, the exit code is recorded,
and the command is killed if it runs longer than `timeout` seconds
(timeout reports exit code `-1`).

## Config

`watchrun.json` (JSON, all keys optional — see
[`watchrun.json.example`](watchrun.json.example)):

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `watch_paths` | list of strings | `["."]` | Files or directories to watch |
| `include` | list of globs | `[]` (= all files) | Only files matching a pattern are watched |
| `exclude` | list of globs | `[]` | Files matching a pattern are ignored (wins over `include`) |
| `command` | string | `echo "changed: {path} ({event})"` | Shell command to run on change |
| `debounce` | number (seconds) | `0.5` | Quiet period before a run |
| `poll_interval` | number (seconds) | `1.0` | Seconds between scans |
| `timeout` | number (seconds) | `30.0` | Kill the command after this long |
| `history_file` | string | `.watchrun-history.jsonl` | Where run records are appended |

The defaults above (poll interval 1.0 s, debounce 0.5 s, timeout 30 s)
are *chosen* defaults, not measured values — tune them to your project.

Example config:

```json
{
  "watch_paths": ["src", "tests"],
  "include": ["**/*.py"],
  "exclude": ["**/__pycache__/**", "**/.git/**"],
  "command": "python -m pytest -q",
  "debounce": 0.5,
  "poll_interval": 1.0,
  "timeout": 30.0,
  "history_file": ".watchrun-history.jsonl"
}
```

Invalid config is rejected loudly: malformed JSON, unknown keys, wrong
types, negative `debounce`, or non-positive `poll_interval`/`timeout`
all fail with a clear error message instead of silently misbehaving.

## Glob patterns

Patterns match the path *relative to the watch root*, with `/` as the
separator:

- `*` — any characters except `/` (one path segment)
- `?` — one character except `/`
- `[abc]` / `[!abc]` — character classes
- `**` — any characters *including* `/`; `**/*.py` matches `a.py` and
  `deep/nested/b.py`, `src/**` matches everything under `src/`

A pattern without a `/` (e.g. `*.py`) is also matched against the
basename, so it applies at any depth. Hidden files (`.hidden.py`) are
matched like any other file — `*` matches a leading dot.

## CLI

```
watchrun init [--path PATH] [--force]
    Scaffold a starter watchrun.json (defaults). Refuses to overwrite
    an existing file unless --force is given.

watchrun watch [--config PATH] [--once] [--verbose]
    Poll and run the command on change. --once performs a single
    scan+run cycle and exits (handy for smoke tests). --verbose logs
    each detected event and run to stderr.

watchrun run-once [--config PATH]
    Run the configured command once immediately (event "manual",
    path = first watch path), without scanning.
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Success (watch stopped cleanly, init wrote the file, run-once command exited 0) |
| `1` | Config error (missing / malformed / invalid config), init target exists, or a run-once command failed without its own exit code |
| `2` | Argument error (argparse default) |
| `124` | run-once command timed out |
| other | `run-once` exits with the command's own exit code |

A failing command during `watch` does **not** stop the watcher — the
failure is recorded in the history log and watching continues.

## Run history

Every run appends one JSON object per line to `history_file`
(default `.watchrun-history.jsonl`, gitignored):

```json
{"timestamp": "2026-11-26T02:44:00+00:00", "path": "/home/me/proj/src/app.py",
 "event": "modified", "command": "python -m pytest -q", "exit_code": 0,
 "timed_out": false, "duration": 1.234, "events": 3,
 "output_tail": "...last 20 lines of combined stdout+stderr..."}
```

`events` is how many change events were coalesced into that run.
`output_tail` keeps only the last 20 lines so chatty commands don't
bloat the log. Corrupt lines are skipped when reading. The history
file itself is automatically excluded from watching, so appending a
run record never triggers another run.

## Project layout

```
watchrun/
├── watchrun/
│   ├── __init__.py    # version
│   ├── __main__.py    # `python -m watchrun`
│   ├── scanner.py     # snapshots + change detection (mtime/size/hash)
│   ├── matcher.py     # glob include/exclude matching (** support)
│   ├── config.py      # JSON config load/validate/defaults/init
│   ├── runner.py      # command execution, timeout, {path}/{event}
│   ├── history.py     # JSON-lines history log
│   ├── watcher.py     # Watcher: poll loop, debounce, run-once
│   └── cli.py         # argparse entry point
├── tests/
│   └── test_watchrun.py
├── watchrun.json.example
└── README.md
```

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

The suite (28 tests, `unittest`, temp directories only) covers:
change detection on write (and no event when nothing changed), added /
deleted detection, a bare `touch` producing no event, debounce
coalescing rapid writes into one run (fake clock — no real sleeps),
glob matching (include/exclude, `**` recursion, hidden files),
command stdout/exit-code capture, timeout killing a long command,
`{path}` / `{event}` substitution, config defaults / invalid JSON /
invalid values / `init` round-trip, and history log round-trip
(including corrupt-line tolerance).

## Limitations

- **Polling, not inotify.** Changes are only noticed at the next poll
  (default every 1.0 s), so detection latency is up to one poll
  interval, and scanning a very large tree every second costs CPU.
  For huge trees, narrow `watch_paths` and `include` patterns.
- **Hash only on mtime/size change.** A file whose content changes
  while its mtime *and* size stay byte-identical will not be detected.
  In practice editors and compilers always bump mtime.
- **Timeout kills only the direct child.** A command that spawns
  detached grandchildren may leave them running after a timeout kill.
- **One run per batch.** If several files change at once, the command
  runs once, templated with the most recent change — it does not run
  once per file.
- **Shell commands.** The command string runs through the shell
  (`shell=True`); quote `{path}` yourself if paths may contain spaces.
