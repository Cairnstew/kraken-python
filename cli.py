#!/usr/bin/env python3
"""Repo-root convenience entry point: `python cli.py ...`.

Delegates to the package CLI so the console script and this file stay in
sync. See `kraken_api/cli.py` for the full command list.
"""

from __future__ import annotations

import sys

from kraken_api.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))