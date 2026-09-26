#!/usr/bin/env python3
"""
harness_runner.py - Wrapper de delegación al runner canónico v2/harness_verify.py (Q04).
Evita listas de suites duplicadas o divergentes; toda ejecución delega a harness_verify.main().
"""

import os
import sys

V2_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if V2_ROOT not in sys.path:
    sys.path.insert(0, V2_ROOT)

from harness_verify import CANONICAL_SUITES, main  # noqa: E402

__all__ = ["CANONICAL_SUITES", "main"]

if __name__ == "__main__":
    main()
