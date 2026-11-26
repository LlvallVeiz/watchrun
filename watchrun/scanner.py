"""Snapshot building and change detection for watchrun.

How detection works (poll-based, no inotify):

1. A *snapshot* maps each watched file's absolute path to a
   :class:`FileEntry` holding its mtime, size, and SHA-256 content hash.
2. On every poll, a fresh snapshot is built and compared with the
   previous one:
   - path in new snapshot only          -> ``added``
   - path in old snapshot only          -> ``deleted``
   - path in both, mtime or size differ -> the file is re-hashed and
     compared with the stored hash; only a real content change is
     reported as ``modified``. A bare ``touch`` (mtime bump, same
     content) is *not* an event.
3. Hashing is the expensive step, so it is skipped whenever possible:
   if a file's mtime *and* size are unchanged since the last snapshot,
   the previously computed hash is reused and the file is not re-read.

Trade-off (documented, by design): a file whose content changes while
its mtime *and* size stay byte-identical will not be detected, because
the hash is only recomputed when mtime or size moves. In practice an
editor or compiler always bumps the mtime.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .matcher import Matcher

__all__ = ["ChangeEvent", "FileEntry", "build_snapshot", "detect_changes"]

_CHUNK_SIZE = 65536  # bytes read per chunk while hashing


@dataclass
class FileEntry:
    """One observed file: cheap change signals plus a content hash."""

    path: str  # absolute path, forward slashes
    mtime: float
    size: int
    sha256: str | None = None


@dataclass(frozen=True)
class ChangeEvent:
    """A single detected change. ``kind`` is added/modified/deleted."""

    kind: str
    path: str  # absolute path, forward slashes


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(watch_path: str, matcher: Matcher, root: str):
    """Yield absolute file paths under ``watch_path`` passing the matcher.

    ``watch_path`` may be a single file or a directory (walked
    recursively, symlinked directories not followed). ``root`` is the
    directory that relative paths are computed against.
    """
    p = Path(watch_path)
    if p.is_file():
        rel = os.path.relpath(str(p), root).replace(os.sep, "/")
        if matcher.matches(rel):
            yield str(p)
        return
    if not p.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if matcher.matches(rel):
                yield full


def build_snapshot(
    watch_paths: list[str],
    matcher: Matcher,
    previous: dict[str, FileEntry] | None = None,
    skip_paths: set[str] | None = None,
) -> dict[str, FileEntry]:
    """Scan all watch paths and return ``{abs_path: FileEntry}``.

    When ``previous`` is given, a file whose mtime and size are unchanged
    reuses its old hash instead of being re-read. ``skip_paths`` holds
    absolute (forward-slash) paths to ignore entirely — used to keep
    watchrun's own history file from triggering runs on itself.
    """
    previous = previous or {}
    skip_paths = skip_paths or set()
    snapshot: dict[str, FileEntry] = {}
    for watch_path in watch_paths:
        p = Path(watch_path)
        root = str(p) if p.is_dir() else str(p.parent)
        for full in _iter_files(watch_path, matcher, root):
            try:
                st = os.stat(full)
            except OSError:
                continue  # vanished between walk and stat; next poll sees it
            abs_posix = os.path.abspath(full).replace(os.sep, "/")
            if abs_posix in skip_paths:
                continue  # e.g. watchrun's own history file
            old = previous.get(abs_posix)
            if (
                old is not None
                and old.mtime == st.st_mtime
                and old.size == st.st_size
                and old.sha256 is not None
            ):
                sha = old.sha256  # cheap path: mtime+size unchanged
            else:
                try:
                    sha = _hash_file(full)
                except OSError:
                    continue
            snapshot[abs_posix] = FileEntry(
                path=abs_posix, mtime=st.st_mtime, size=st.st_size, sha256=sha
            )
    return snapshot


def detect_changes(
    old: dict[str, FileEntry], new: dict[str, FileEntry]
) -> list[ChangeEvent]:
    """Diff two snapshots into added/modified/deleted events.

    Sorted by path so output is deterministic.
    """
    events: list[ChangeEvent] = []
    for path in sorted(new):
        if path not in old:
            events.append(ChangeEvent("added", path))
        else:
            o, n = old[path], new[path]
            if o.sha256 is not None and n.sha256 is not None:
                changed = o.sha256 != n.sha256
            else:  # hash unavailable: fall back to mtime/size
                changed = (o.mtime != n.mtime) or (o.size != n.size)
            if changed:
                events.append(ChangeEvent("modified", path))
    for path in sorted(old):
        if path not in new:
            events.append(ChangeEvent("deleted", path))
    return events
