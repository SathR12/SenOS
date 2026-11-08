"""Standard-library-only benchmark workloads; never use production application data."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import time


def cpu_work(iterations: int) -> None:
    value = b"SenOS fixed CPU benchmark input"
    for _ in range(iterations):
        value = hashlib.sha256(value).digest()


def publish(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def serve(ready: Path, iterations: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/work":
                self.send_error(404)
                return
            cpu_work(iterations)
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        publish(ready, {"port": server.server_port})
        server.serve_forever(poll_interval=0.1)


def background(progress: Path, iterations: int) -> None:
    completed = 0
    published = 0.0
    while True:
        cpu_work(iterations)
        completed += 1
        now = time.monotonic()
        if now - published >= 0.1:
            publish(progress, {"completed_batches": completed, "monotonic": now})
            published = now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("web", "background"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=20000)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10000000:
        parser.error("iterations must be between 1 and 10000000")
    if args.mode == "web":
        serve(args.state, args.iterations)
    else:
        background(args.state, args.iterations)


if __name__ == "__main__":
    main()
