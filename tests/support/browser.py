import os
from pathlib import Path


def browser_options():
    channel = os.environ.get("API_TEST_BROWSER_CHANNEL")
    if not channel and os.name == "nt":
        roots = {os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
                 os.environ.get("ProgramFiles", "C:/Program Files")}
        if any((Path(root) / "Microsoft/Edge/Application/msedge.exe").is_file() for root in roots):
            channel = "msedge"
    return {"headless": True, **({"channel": channel} if channel else {})}
