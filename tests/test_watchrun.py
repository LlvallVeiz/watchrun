"""Tests for watchrun — real unittest tests on temp directories.

Run from the project root:  python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchrun.config import Config, ConfigError, load_config, write_init_config
from watchrun.history import append_history, read_history
from watchrun.matcher import Matcher
from watchrun.runner import run_command, substitute
from watchrun.scanner import build_snapshot, detect_changes
from watchrun.watcher import Watcher


class FakeClock:
    """Injectable clock: tests advance time explicitly, no real sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _write(path: Path, text: str, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.matcher = Matcher()

    def tearDown(self):
        self.tmp.cleanup()

    def test_modified_detected_on_write(self):
        target = self.root / "a.py"
        _write(target, "x = 1\n", mtime=1_000_000)
        old = build_snapshot([str(self.root)], self.matcher)
        _write(target, "x = 2\n", mtime=1_000_100)
        new = build_snapshot([str(self.root)], self.matcher, previous=old)
        events = detect_changes(old, new)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "modified")
        self.assertTrue(events[0].path.endswith("a.py"))

    def test_no_change_produces_no_event(self):
        _write(self.root / "a.py", "x = 1\n", mtime=1_000_000)
        old = build_snapshot([str(self.root)], self.matcher)
        new = build_snapshot([str(self.root)], self.matcher, previous=old)
        self.assertEqual(detect_changes(old, new), [])

    def test_touch_same_content_is_not_an_event(self):
        # mtime bumped, content identical -> hash unchanged -> no event.
        target = self.root / "a.py"
        _write(target, "x = 1\n", mtime=1_000_000)
        old = build_snapshot([str(self.root)], self.matcher)
        os.utime(target, (1_000_500, 1_000_500))
        new = build_snapshot([str(self.root)], self.matcher, previous=old)
        self.assertEqual(detect_changes(old, new), [])

    def test_added_and_deleted_detected(self):
        _write(self.root / "keep.py", "k = 1\n", mtime=1_000_000)
        _write(self.root / "gone.py", "g = 1\n", mtime=1_000_000)
        old = build_snapshot([str(self.root)], self.matcher)
        _write(self.root / "new.py", "n = 1\n", mtime=1_000_100)
        (self.root / "gone.py").unlink()
        new = build_snapshot([str(self.root)], self.matcher, previous=old)
        kinds = {e.kind: e.path for e in detect_changes(old, new)}
        self.assertIn("added", kinds)
        self.assertTrue(kinds["added"].endswith("new.py"))
        self.assertIn("deleted", kinds)
        self.assertTrue(kinds["deleted"].endswith("gone.py"))

    def test_single_file_watch_path(self):
        target = self.root / "only.py"
        _write(target, "a = 1\n", mtime=1_000_000)
        old = build_snapshot([str(target)], self.matcher)
        _write(target, "a = 2\n", mtime=1_000_100)
        new = build_snapshot([str(target)], self.matcher, previous=old)
        self.assertEqual(
            [e.kind for e in detect_changes(old, new)], ["modified"]
        )

    def test_skip_paths_ignored(self):
        # The history file lives inside the watched tree; it must never
        # appear in a snapshot, or runs would trigger themselves.
        target = self.root / "skip.jsonl"
        _write(target, "x\n", mtime=1_000_000)
        skip = {os.path.abspath(str(target)).replace(os.sep, "/")}
        snap = build_snapshot(
            [str(self.root)], self.matcher, skip_paths=skip
        )
        self.assertEqual(snap, {})


class MatcherTests(unittest.TestCase):
    def test_include_and_exclude(self):
        m = Matcher(include=["**/*.py"], exclude=["**/test_*.py"])
        self.assertTrue(m.matches("main.py"))
        self.assertTrue(m.matches("pkg/mod.py"))
        self.assertFalse(m.matches("notes.txt"))
        self.assertFalse(m.matches("test_main.py"))
        self.assertFalse(m.matches("pkg/test_mod.py"))

    def test_double_star_recursion(self):
        m = Matcher(include=["src/**/*.py"])
        self.assertTrue(m.matches("src/a.py"))
        self.assertTrue(m.matches("src/deep/nested/b.py"))
        self.assertFalse(m.matches("other/a.py"))
        self.assertFalse(m.matches("src/a.txt"))

    def test_bare_pattern_matches_any_depth(self):
        m = Matcher(include=["*.py"])
        self.assertTrue(m.matches("top.py"))
        self.assertTrue(m.matches("deep/nested/mod.py"))

    def test_star_does_not_cross_slash(self):
        m = Matcher(include=["pkg/*.py"])
        self.assertTrue(m.matches("pkg/mod.py"))
        self.assertFalse(m.matches("pkg/sub/mod.py"))

    def test_hidden_files_are_matched(self):
        m = Matcher(include=["*.py"])
        self.assertTrue(m.matches(".hidden.py"))
        self.assertTrue(m.matches(".config/app.py"))

    def test_empty_include_matches_everything(self):
        m = Matcher(exclude=["*.log"])
        self.assertTrue(m.matches("anything.txt"))
        self.assertFalse(m.matches("debug.log"))


class DebounceTests(unittest.TestCase):
    def _watcher(self, root: Path, history: Path) -> tuple[Watcher, FakeClock]:
        clock = FakeClock()
        cfg = Config(
            watch_paths=[str(root)],
            command="echo changed {path} {event}",
            debounce=0.5,
            poll_interval=0.1,
            history_file=str(history),
        )
        return Watcher(cfg, clock=clock, sleeper=lambda _: None), clock

    def test_rapid_writes_coalesce_into_one_run(self):
        with tempfile.TemporaryDirectory() as td:
            root, history = Path(td), Path(td) / "hist.jsonl"
            _write(root / "a.py", "v1\n", mtime=1_000_000)
            watcher, clock = self._watcher(root, history)
            watcher.poll_once()  # baseline: everything is "added"
            watcher.flush()  # clear the baseline run
            self.assertEqual(len(read_history(str(history))), 1)

            # Three rapid writes, all inside the debounce window.
            for i, text in enumerate(("v2\n", "v3\n", "v4\n")):
                _write(root / "a.py", text, mtime=1_000_100 + i)
                watcher.poll_once()
                clock.advance(0.1)
                self.assertIsNone(watcher.maybe_run())  # still waiting

            clock.advance(0.5)  # quiet period reached
            result = watcher.maybe_run()
            self.assertIsNotNone(result)
            self.assertEqual(result.exit_code, 0)
            # Exactly one extra history record for the three writes.
            self.assertEqual(len(read_history(str(history))), 2)
            self.assertEqual(read_history(str(history))[-1]["events"], 3)
            self.assertIsNone(watcher.maybe_run())  # nothing left pending

    def test_quiet_period_restarts_on_new_change(self):
        with tempfile.TemporaryDirectory() as td:
            root, history = Path(td), Path(td) / "hist.jsonl"
            _write(root / "a.py", "v1\n", mtime=1_000_000)
            watcher, clock = self._watcher(root, history)
            watcher.poll_once()
            watcher.flush()
            _write(root / "a.py", "v2\n", mtime=1_000_100)
            watcher.poll_once()
            clock.advance(0.4)
            self.assertIsNone(watcher.maybe_run())
            # Another change resets the quiet-period clock.
            _write(root / "a.py", "v3\n", mtime=1_000_200)
            watcher.poll_once()
            clock.advance(0.4)
            self.assertIsNone(watcher.maybe_run())
            clock.advance(0.2)
            self.assertIsNotNone(watcher.maybe_run())


class RunnerTests(unittest.TestCase):
    def test_captures_stdout_and_exit_code(self):
        result = run_command("echo hello && echo oops >&2; exit 3")
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr.strip(), "oops")
        self.assertEqual(result.exit_code, 3)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.timed_out)

    def test_success_exit_code_zero(self):
        result = run_command("echo ok")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.succeeded)

    def test_timeout_kills_long_command(self):
        result = run_command("sleep 5", timeout=0.3)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, -1)
        self.assertLess(result.duration, 5.0)

    def test_placeholder_substitution(self):
        self.assertEqual(
            substitute("echo {path} {event}", path="/x/a.py", event="modified"),
            "echo /x/a.py modified",
        )
        result = run_command(
            "echo {path} {event}", path="/x/a.py", event="added"
        )
        self.assertEqual(result.stdout.strip(), "/x/a.py added")

    def test_duration_is_measured(self):
        result = run_command("sleep 0.2", timeout=5)
        self.assertGreaterEqual(result.duration, 0.15)


class ConfigTests(unittest.TestCase):
    def test_defaults_applied(self):
        cfg = Config.from_dict({})
        self.assertEqual(cfg.watch_paths, ["."])
        self.assertEqual(cfg.include, [])
        self.assertEqual(cfg.command, 'echo "changed: {path} ({event})"')
        self.assertEqual(cfg.debounce, 0.5)
        self.assertEqual(cfg.poll_interval, 1.0)
        self.assertEqual(cfg.timeout, 30.0)
        self.assertEqual(cfg.history_file, ".watchrun-history.jsonl")

    def test_partial_config_merges_with_defaults(self):
        cfg = Config.from_dict({"command": "make test", "debounce": 2})
        self.assertEqual(cfg.command, "make test")
        self.assertEqual(cfg.debounce, 2.0)
        self.assertEqual(cfg.poll_interval, 1.0)  # default kept

    def test_invalid_json_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(str(bad))

    def test_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                load_config(str(Path(td) / "nope.json"))

    def test_invalid_values_rejected(self):
        for data in (
            {"debounce": -1},
            {"poll_interval": 0},
            {"timeout": "soon"},
            {"watch_paths": []},
            {"command": ""},
            {"unknown_key": 1},
        ):
            with self.subTest(data=data):
                with self.assertRaises(ConfigError):
                    Config.from_dict(data)

    def test_init_writes_valid_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "watchrun.json"
            write_init_config(str(path))
            cfg = load_config(str(path))  # must parse back cleanly
            self.assertEqual(cfg.debounce, 0.5)
            self.assertEqual(cfg.watch_paths, ["."])
            # Refuses to overwrite without --force.
            with self.assertRaises(ConfigError):
                write_init_config(str(path))
            write_init_config(str(path), overwrite=True)  # force works


class HistoryTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            hist = str(Path(td) / "hist.jsonl")
            append_history(
                hist,
                path="/x/a.py",
                event="modified",
                command="echo /x/a.py",
                exit_code=0,
                duration=0.12,
                stdout="hello\nworld\n",
                events=2,
            )
            records = read_history(hist)
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec["path"], "/x/a.py")
            self.assertEqual(rec["event"], "modified")
            self.assertEqual(rec["exit_code"], 0)
            self.assertEqual(rec["events"], 2)
            self.assertIn("hello", rec["output_tail"])
            self.assertIn("timestamp", rec)

    def test_missing_file_reads_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(read_history(str(Path(td) / "nope.jsonl")), [])

    def test_corrupt_line_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "hist.jsonl"
            hist.write_text(
                '{"path": "ok"}\nthis is not json\n{"path": "also-ok"}\n',
                encoding="utf-8",
            )
            self.assertEqual(len(read_history(str(hist))), 2)

    def test_output_tail_keeps_last_lines(self):
        with tempfile.TemporaryDirectory() as td:
            hist = str(Path(td) / "hist.jsonl")
            big = "\n".join(f"line {i}" for i in range(50))
            rec = append_history(
                hist,
                path="/x/a.py",
                event="manual",
                command="echo",
                exit_code=0,
                duration=0.01,
                stdout=big,
            )
            self.assertNotIn("line 0", rec["output_tail"])
            self.assertIn("line 49", rec["output_tail"])


if __name__ == "__main__":
    unittest.main()
