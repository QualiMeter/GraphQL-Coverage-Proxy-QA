import os
from pathlib import Path

from coverage_qa.control_panel import Manager, create_app

app = create_app(Manager(Path(os.environ["QA_TEST_REPORTS"])))


@app.get("/health")
def health():
    return {"status": "ok"}
