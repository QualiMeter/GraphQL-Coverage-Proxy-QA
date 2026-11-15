"""Single command for tests, the control panel and saved reports."""
import sys


def main():
    commands = {"test": "runner", "panel": "control_panel", "reports": "reporting.viewer"}
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("Usage: coverage-qa {test|panel|reports} [options]\nUse coverage-qa COMMAND --help for details.")
        return 0
    command = sys.argv.pop(1)
    if command not in commands:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    from importlib import import_module
    return import_module("coverage_qa." + commands[command]).main()
