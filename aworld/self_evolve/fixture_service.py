from __future__ import annotations

import argparse
import os
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_PARENT_POLL_INTERVAL_SECONDS = 0.1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--transport",
        choices=("http_fixture", "tcp_fixture"),
        required=True,
    )
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if args.parent_pid is not None and args.parent_pid <= 1:
        parser.error("--parent-pid must identify a live parent process")
    fixture = Path(args.fixture).read_bytes()
    if args.transport == "http_fixture":
        server = _http_server(args.port, fixture)
    else:
        server = _tcp_server(args.port, fixture)
    try:
        _serve(server, parent_pid=args.parent_pid)
    finally:
        server.server_close()
    return 0


def _serve(
    server: socketserver.BaseServer,
    *,
    parent_pid: int | None,
) -> None:
    if parent_pid is None:
        server.serve_forever()
        return

    # Framework-owned fixture services run directly instead of behind the
    # replay service supervisor.  Keep parent liveness checks in this process
    # so a hard-killed optimize run cannot leave an orphaned listener behind.
    # A short handle_request timeout bounds cleanup latency even when the
    # fixture receives no traffic.
    server.timeout = _PARENT_POLL_INTERVAL_SECONDS
    while _parent_is_alive(parent_pid):
        server.handle_request()


def _parent_is_alive(parent_pid: int) -> bool:
    # The ppid check prevents PID reuse from attaching an orphaned fixture to
    # an unrelated process.  kill(pid, 0) also catches a missing parent before
    # reparenting becomes visible to this process.
    if os.getppid() != parent_pid:
        return False
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
