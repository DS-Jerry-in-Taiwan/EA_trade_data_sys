#!/usr/bin/env python3
"""Compatibility launcher; the supervisor now belongs to service.runtime."""

import sys
from pathlib import Path

# Direct execution puts only mt5docker/ on sys.path. Keep this migration shim
# usable while operators move to ``python -m service.runtime.supervisor``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.runtime.supervisor import main


if __name__ == "__main__":
    raise SystemExit(main())
