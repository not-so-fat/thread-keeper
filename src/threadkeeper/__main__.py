"""Allow ``python -m threadkeeper`` (used as the install-hooks PATH fallback)."""

from .cli import main

if __name__ == "__main__":
    main()
