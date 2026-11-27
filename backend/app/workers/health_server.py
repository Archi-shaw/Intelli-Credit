"""Minimal HTTP health endpoint for running the Celery worker on platforms that require a listening port
(e.g. Google Cloud Run services). Exits when the worker process dies so the platform restarts the instance.

  python -m app.workers.health_server --pid <celery pid> --port $PORT
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = ap.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            ok = _alive(args.pid)
            self.send_response(200 if ok else 503)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","component":"worker"}' if ok else b'{"status":"worker_down"}')

        def log_message(self, *a):  # silence per-request logs
            pass

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while _alive(args.pid):
        time.sleep(5)
    server.shutdown()
    sys.exit(1)


if __name__ == "__main__":
    main()
