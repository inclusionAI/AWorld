from __future__ import annotations

import argparse
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--transport",
        choices=("http_fixture", "tcp_fixture"),
        required=True,
    )
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    fixture = Path(args.fixture).read_bytes()
    if args.transport == "http_fixture":
        server = _http_server(args.port, fixture)
    else:
        server = _tcp_server(args.port, fixture)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _http_server(port: int, fixture: bytes) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(fixture)))
            self.end_headers()
            self.wfile.write(fixture)

        def log_message(self, *_args: object) -> None:
            return

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _tcp_server(port: int, fixture: bytes) -> socketserver.ThreadingTCPServer:
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(1.0)
            try:
                self.request.recv(1024 * 1024)
                self.request.sendall(fixture)
            except (BrokenPipeError, ConnectionError, TimeoutError):
                return

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    return Server(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    raise SystemExit(main())
