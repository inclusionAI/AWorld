#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

GATEWAY_KEYS = (
    "action_result",
    "tool_outputs",
    "content",
    "response",
    "result",
    "output",
    "body",
    "data",
)


def load_fixture(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
        return json.loads(text)
    except Exception:
        return raw.decode("utf-8", errors="replace")


def project_fixture_value(data, max_depth=32):
    if max_depth <= 0:
        return None
    if isinstance(data, dict):
        for key in GATEWAY_KEYS:
            if key in data and data[key] is not None:
                inner = project_fixture_value(data[key], max_depth - 1)
                if inner is not None:
                    return inner
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) and v not in ("", 0, False):
                return v
        return None
    if isinstance(data, list):
        for item in data[:8]:
            inner = project_fixture_value(item, max_depth - 1)
            if inner is not None:
                return inner
        return None
    if isinstance(data, (str, int, float, bool)) and data not in ("", 0, False):
        return data
    if data is None:
        return None
    return str(data)[:8192]


def project_fixture_container(data, max_depth=32):
    if max_depth <= 0:
        return data
    if isinstance(data, dict):
        for key in GATEWAY_KEYS:
            if key in data and data[key] is not None:
                return project_fixture_container(data[key], max_depth - 1)
        return data
    if isinstance(data, list):
        for item in data[:8]:
            inner = project_fixture_container(item, max_depth - 1)
            if isinstance(inner, dict):
                return inner
        return data
    return data


class ReplayRuntime:
    def __init__(self, fixture_path, scratch_dir, port):
        self.fixture_path = fixture_path
        self.scratch_dir = scratch_dir
        self.port = port
        self.fixture_data = load_fixture(fixture_path)
        self.sequence = 0
        self.trace_path = os.path.join(scratch_dir, "protocol_trace.jsonl")
        self.trace_file = None
        self._open_trace()

    def _open_trace(self):
        self.trace_file = open(self.trace_path, "w", encoding="utf-8")

    def next_seq(self):
        self.sequence += 1
        return self.sequence

    def write_trace(self, direction, kind, fields=None, correlation=None, path=None):
        entry = {
            "direction": direction,
            "sequence": self.next_seq(),
            "kind": kind,
            "fields": sorted(fields) if fields else [],
            "correlation": correlation if correlation is not None else {},
        }
        if path is not None:
            entry["path"] = path
        if self.trace_file:
            self.trace_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.trace_file.flush()
        return entry

    def read_sidecar_records(self):
        env_path = os.environ.get("AWORLD_REPLAY_RESPONSE_INDEX", "")
        if env_path and os.path.isfile(env_path):
            with open(env_path, "r", encoding="utf-8") as index_file:
                index_doc = json.load(index_file)
            if isinstance(index_doc, list):
                records = index_doc
            elif isinstance(index_doc, dict):
                records = index_doc.get("records", [])
            else:
                records = []
            projected_values = []
            for record in records:
                if isinstance(record, dict) and "value" in record:
                    projected_values.append(record["value"])
            if projected_values:
                return projected_values
        return None

    def build_payload(self):
        sidecar = self.read_sidecar_records()
        if sidecar is not None:
            return {"values": sidecar}
        container = project_fixture_container(self.fixture_data)
        scalar = project_fixture_value(self.fixture_data)
        if isinstance(container, dict) and scalar is not None:
            if "content" not in container:
                container["content"] = scalar
            return container
        if scalar is not None:
            return {"content": scalar}
        return {"content": str(self.fixture_data)[:4096]}

    def get_response_contains(self):
        sidecar = self.read_sidecar_records()
        if sidecar:
            return sidecar[0] if isinstance(sidecar[0], str) else json.dumps(sidecar[0], ensure_ascii=False)
        val = project_fixture_value(self.fixture_data)
        if val is None:
            val = str(self.fixture_data)[:4096]
        return str(val)[:8192]

    def close(self):
        if self.trace_file:
            self.trace_file.close()


class Handler(BaseHTTPRequestHandler):
    runtime = None

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        correlation = {}
        qs = parse_qs(parsed.query)
        if qs:
            for k, v in qs.items():
                correlation[k] = v[0] if len(v) == 1 else v
        self.runtime.write_trace(
            "inbound", "http_get",
            fields=["path"],
            correlation=correlation,
            path=parsed.path,
        )
        if parsed.path == "/healthz":
            payload = {"status": "ok"}
            self.runtime.write_trace(
                "outbound", "http_response",
                fields=list(payload.keys()),
                correlation=correlation,
            )
            self._send_json(200, payload)
            return
        payload = self.runtime.build_payload()
        self.runtime.write_trace(
            "outbound", "http_response",
            fields=list(payload.keys()) if isinstance(payload, dict) else ["content"],
            correlation=correlation,
        )
        self._send_json(200, payload)

    def do_POST(self):
        parsed = urlparse(self.path)
        correlation = {}
        length = int(self.headers.get("Content-Length", 0))
        raw_body = b""
        if length:
            raw_body = self.rfile.read(length)
        try:
            req_body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            req_body = {}
        if isinstance(req_body, dict):
            for key in ("request_id", "id", "requestId", "trace_id", "traceId", "session", "channel", "routing"):
                if key in req_body:
                    correlation[key] = req_body[key]
        self.runtime.write_trace(
            "inbound", "http_post",
            fields=sorted(req_body.keys()) if isinstance(req_body, dict) else [],
            correlation=correlation,
            path=parsed.path,
        )
        payload = self.runtime.build_payload()
        self.runtime.write_trace(
            "outbound", "http_response",
            fields=list(payload.keys()) if isinstance(payload, dict) else ["content"],
            correlation=correlation,
        )
        self._send_json(200, payload)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--scratch", required=True)
    args = parser.parse_args()

    os.makedirs(args.scratch, exist_ok=True)
    runtime = ReplayRuntime(args.fixture, args.scratch, args.port)
    Handler.runtime = runtime

    runtime.write_trace("outbound", "runtime_start", fields=["port"], correlation={})

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        runtime.write_trace(
            "outbound", "runtime_error",
            fields=["error"],
            correlation={"error": str(exc)[:512]},
        )
        sys.stderr.write(json.dumps({"event": "runtime_error", "error": str(exc)[:512]}) + "\n")
        sys.stderr.flush()
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    main()