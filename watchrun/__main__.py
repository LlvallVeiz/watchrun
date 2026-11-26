"""Entry point so ``python -m watchrun`` works."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
