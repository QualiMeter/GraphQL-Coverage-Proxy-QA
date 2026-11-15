from pathlib import Path


def group_for(path):
    parts = Path(path).parts
    if "tests" in parts:
        parts = parts[len(parts) - tuple(reversed(parts)).index("tests"):]
    if "live" in parts:
        return "live"
    if "e2e" in parts:
        return "e2e"
    if "api" in parts and "coverage" in parts:
        return "coverage"
    if "api" in parts and "shop" in parts:
        return "business"
    return "tooling"


def discover(directory, suite, include_live=False):
    groups = {"coverage", "business", "e2e", "tooling"} if suite == "all" else {suite}
    if include_live:
        groups.add("live")
    return [p for p in sorted(Path(directory).rglob("test_*.py")) if "support" not in p.parts and group_for(p) in groups]
