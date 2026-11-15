"""View saved reports over loopback HTTP. No API calls or mutations are performed."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from coverage_qa.environment import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9010)
    args = parser.parse_args()
    folder = ROOT / "reports"
    if not (folder / "index.html").exists():
        parser.error("Run tests first: no report history exists")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(folder)))
    print(f"Reports: http://localhost:{args.port}/\nCtrl+C stops this read-only viewer.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
