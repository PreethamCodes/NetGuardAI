"""Minimal HTTP server serving an in-memory payload (no files on disk).

    python -m netguard.traffic.workers.http_server --bind 10.0.0.5 --port 8000 --payload-kb 64
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Sequence


def make_handler(payload: bytes) -> type:
    """Return a request-handler class that serves ``payload`` at ``/payload``."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path.split("?", 1)[0] == "/payload":
                body, status = payload, 200
            elif self.path == "/":
                body, status = b"netguard http server\n", 200
            else:
                body, status = b"not found\n", 404
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # silence per-request logging
            pass

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bind", required=True, help="IPv4 address of the Mininet host")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--payload-kb", type=int, default=64)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    payload = b"x" * (args.payload_kb * 1024)
    server = _Server((args.bind, args.port), make_handler(payload))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
