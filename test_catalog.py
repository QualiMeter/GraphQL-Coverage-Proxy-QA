"""Shared discovery rules for the CLI launcher and the local panel."""

from pathlib import Path


def group_for(path):
    name = Path(path).name
    if name.startswith(("test_coverage", "test_browser")):
        return "coverage"
    if name.startswith("test_business"):
        return "business"
    if name.startswith("test_live"):
        return "live"
    if name.startswith("test_shop"):
        return "legacy"
    return "tooling"


def discover(directory, suite, include_live=False):
    groups = {"coverage", "business", "tooling"} if suite == "all" else {"legacy" if suite == "shop" else suite}
    if include_live:
        groups.add("live")
    return [path for path in sorted(Path(directory).glob("test_*.py")) if group_for(path) in groups]
