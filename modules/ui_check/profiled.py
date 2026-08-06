#!/usr/bin/env python3
"""Profile-aware UI check entry point.

Auto-detection applies no specialized UI rules to unrelated projects. Operators
may explicitly request ``typescript-vite``, ``threejs`` or ``tower-stack`` via
``--profile``. The legacy enforcer remains available as an explicit compatibility
command for projects that still depend on its historical five-rule contract.
"""
from __future__ import annotations

from modules.delivery.profiles import main


if __name__ == "__main__":
    raise SystemExit(main())
