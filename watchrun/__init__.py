"""watchrun — a small poll-based file-watch command runner.

Watches files and directories for changes and runs a shell command when
something changes. Standard library only; no third-party watcher, no
inotify — change detection is plain polling (see :mod:`watchrun.scanner`).
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
