"""Glob include/exclude matching for watchrun.

Patterns are matched against the path of a file *relative to the watch
root it was found under*, using forward slashes. Supported syntax:

- ``*``  — any run of characters except ``/`` (one path segment)
- ``?``  — any single character except ``/``
- ``[...]`` — a character class (``[!...]`` negates it)
- ``**`` — any run of characters *including* ``/`` (any depth).
  ``**/*.py`` matches ``a.py`` and ``deep/nested/b.py``; ``src/**``
  matches everything under ``src/``.

A pattern that contains no ``/`` (for example ``*.py``) is matched
against the file's basename as well, so it applies at any depth.
Hidden files (names starting with ``.``) are matched like any other
file — ``*`` matches a leading dot, unlike shell globbing.

An empty include list means "include everything". Excludes always win
over includes.
"""

from __future__ import annotations

import os
import re

__all__ = ["Matcher", "compile_glob", "matches_any"]


def _glob_to_regex(pattern: str) -> str:
    """Translate one glob pattern (with ``**`` support) to a regex string."""
    i, n = 0, len(pattern)
    out: list[str] = []
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # "**": any characters including "/".
                # "**/" at a segment boundary matches zero or more dirs.
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] == "!":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                # Unterminated class: treat "[" literally.
                out.append(re.escape("["))
                i += 1
            else:
                stuff = pattern[i + 1 : j]
                if stuff.startswith("!"):
                    stuff = "^" + stuff[1:]
                out.append("[" + stuff + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def compile_glob(pattern: str) -> "re.Pattern[str]":
    """Compile one glob pattern to a regex matching a whole rel path.

    Patterns without a ``/`` also match the basename, so ``*.py`` applies
    at any depth below the watch root.
    """
    pattern = pattern.replace(os.sep, "/")
    body = _glob_to_regex(pattern)
    if "/" not in pattern:
        # Match either the whole relative path or just the basename.
        return re.compile(r"(?:^|.*/)" + body + r"$")
    return re.compile("^" + body + "$")


def matches_any(patterns: list["re.Pattern[str]"], rel_path: str) -> bool:
    """True if any compiled pattern matches ``rel_path``."""
    posix = rel_path.replace(os.sep, "/")
    return any(rx.match(posix) for rx in patterns)


class Matcher:
    """Include/exclude matcher over paths relative to a watch root."""

    def __init__(
        self,
        include: list[str] | tuple[str, ...] = (),
        exclude: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.include_patterns = list(include)
        self.exclude_patterns = list(exclude)
        self._include = [compile_glob(p) for p in include]
        self._exclude = [compile_glob(p) for p in exclude]

    def matches(self, rel_path: str) -> bool:
        """True if ``rel_path`` passes the include/exclude rules."""
        if self._include and not matches_any(self._include, rel_path):
            return False
        if self._exclude and matches_any(self._exclude, rel_path):
            return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Matcher(include={self.include_patterns!r}, "
            f"exclude={self.exclude_patterns!r})"
        )
