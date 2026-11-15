"""Check that the team's tracked input files were not changed by test tooling."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main():
    manifest = json.loads((ROOT / "source-baseline.json").read_text(encoding="utf-8"))
    changed = []
    for name, expected in manifest.items():
        file = ROOT.parent / name
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            changed.append(name)
    print(f"Checked {len(manifest)} original backend/frontend files: {len(changed)} changed.")
    for name in changed:
        print(name)
    return int(bool(changed))


if __name__ == "__main__":
    raise SystemExit(main())
