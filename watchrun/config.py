"""JSON configuration for watchrun (standard library only).

Schema (all keys optional — missing keys take the defaults)::

    {
      "watch_paths": ["."],            // files or dirs to watch
      "include": ["**/*.py"],          // glob patterns, empty = all files
      "exclude": ["**/__pycache__/**", "**/.git/**"],
      "command": "pytest -q",          // shell command; {path} and {event}
      "debounce": 0.5,                 // seconds of quiet before running
      "poll_interval": 1.0,            // seconds between scans
      "timeout": 30.0,                 // seconds before the command is killed
      "history_file": ".watchrun-history.jsonl"
    }

Defaults are documented in the README as *chosen* defaults, not measured
values: poll interval 1.0 s, debounce 0.5 s, timeout 30 s.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

__all__ = [
    "Config",
    "ConfigError",
    "default_config",
    "example_config",
    "load_config",
    "write_example_config",
    "write_init_config",
]

DEFAULT_COMMAND = 'echo "changed: {path} ({event})"'
DEFAULT_DEBOUNCE = 0.5
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_HISTORY_FILE = ".watchrun-history.jsonl"


class ConfigError(ValueError):
    """Raised when a config file is missing, malformed, or invalid."""


@dataclass
class Config:
    watch_paths: list[str] = field(default_factory=lambda: ["."])
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    command: str = DEFAULT_COMMAND
    debounce: float = DEFAULT_DEBOUNCE
    poll_interval: float = DEFAULT_POLL_INTERVAL
    timeout: float = DEFAULT_TIMEOUT
    history_file: str = DEFAULT_HISTORY_FILE

    @classmethod
    def from_dict(cls, data: dict, source: str = "<config>") -> "Config":
        """Build a Config from a parsed JSON object, validating every key."""
        if not isinstance(data, dict):
            raise ConfigError(f"{source}: top level must be a JSON object")
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ConfigError(f"{source}: unknown key(s): {', '.join(unknown)}")

        cfg = cls()
        if "watch_paths" in data:
            paths = data["watch_paths"]
            if not isinstance(paths, list) or not paths or not all(
                isinstance(p, str) and p for p in paths
            ):
                raise ConfigError(
                    f"{source}: 'watch_paths' must be a non-empty list of strings"
                )
            cfg.watch_paths = list(paths)
        for key in ("include", "exclude"):
            if key in data:
                patterns = data[key]
                if not isinstance(patterns, list) or not all(
                    isinstance(p, str) for p in patterns
                ):
                    raise ConfigError(
                        f"{source}: '{key}' must be a list of strings"
                    )
                setattr(cfg, key, list(patterns))
        if "command" in data:
            if not isinstance(data["command"], str) or not data["command"]:
                raise ConfigError(f"{source}: 'command' must be a non-empty string")
            cfg.command = data["command"]
        for key in ("debounce", "poll_interval", "timeout"):
            if key in data:
                value = data[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigError(f"{source}: '{key}' must be a number")
                if key == "debounce" and value < 0:
                    raise ConfigError(f"{source}: 'debounce' must be >= 0")
                if key in ("poll_interval", "timeout") and value <= 0:
                    raise ConfigError(f"{source}: '{key}' must be > 0")
                setattr(cfg, key, float(value))
        if "history_file" in data:
            if not isinstance(data["history_file"], str) or not data["history_file"]:
                raise ConfigError(
                    f"{source}: 'history_file' must be a non-empty string"
                )
            cfg.history_file = data["history_file"]
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def default_config() -> Config:
    """A Config with every field at its default value."""
    return Config()


def example_config() -> dict:
    """A commented-by-example config dict (JSON has no comments)."""
    cfg = default_config()
    cfg.watch_paths = ["src", "tests"]
    cfg.include = ["**/*.py"]
    cfg.exclude = ["**/__pycache__/**", "**/.git/**"]
    cfg.command = "python -m pytest -q"
    return cfg.to_dict()


def load_config(path: str) -> Config:
    """Load and validate a JSON config file."""
    if not os.path.exists(path):
        raise ConfigError(
            f"config file not found: {path} "
            f"(run `watchrun init` to scaffold one)"
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config: {exc}") from exc
    return Config.from_dict(data, source=path)


def write_example_config(path: str, *, overwrite: bool = False) -> str:
    """Write the example config to ``path``; returns the path written."""
    if os.path.exists(path) and not overwrite:
        raise ConfigError(
            f"{path} already exists (use --force to overwrite it)"
        )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(example_config(), fh, indent=2)
        fh.write("\n")
    return path


def write_init_config(path: str, *, overwrite: bool = False) -> str:
    """Write a minimal starter config (defaults) to ``path``."""
    if os.path.exists(path) and not overwrite:
        raise ConfigError(
            f"{path} already exists (use --force to overwrite it)"
        )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(default_config().to_dict(), fh, indent=2)
        fh.write("\n")
    return path
