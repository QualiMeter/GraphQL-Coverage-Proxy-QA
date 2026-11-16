#!/usr/bin/env python3
"""CLI wrapper to run preflight checks across Frontend, Backend, and QA."""
import sys
from pathlib import Path

# Add src to sys.path so it works when run directly
QA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QA_ROOT / "src"))

from coverage_qa.preflight import main

if __name__ == "__main__":
    main()
