"""Stable executable and import-compatible entrypoint for snorse-bot."""

from __future__ import annotations

import importlib
import sys


if __name__ == "__main__":
    from app.bot import main

    raise SystemExit(main())

# Existing integrations and tests historically import ``main``. Alias the
# package module instead of duplicating its API while downstream users migrate.
sys.modules[__name__] = importlib.import_module("app.bot")
