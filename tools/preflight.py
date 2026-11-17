#!/usr/bin/env python3
import sys
from pathlib import Path

QA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QA_ROOT / "src"))

from coverage_qa.preflight import main

if __name__ == "__main__":
    main()
