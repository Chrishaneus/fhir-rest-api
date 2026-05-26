"""Entry point for ``python -m scripts.seed``."""

from __future__ import annotations

import sys

from scripts.seed.runner import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
