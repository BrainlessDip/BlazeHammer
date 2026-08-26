"""Shared fixtures: local threaded HTTP server, template files."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.current = 0
        self.max_concurrent = 0
        self.flaky_count = 0
        self.bodies: list[dict] = []
        self.seen_headers: list[dict] = []

    def enter(self) -> None:
        with self.lock:
            self.requests += 1
            self.current += 1
            self.max_concurrent = max(self.max_concurrent, self.current)

    def leave(self) -> None:
        with self.lock:
            self.current -= 1


def _make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _respond(self, status, payload, extra_headers=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(length) if length else b""

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            state.enter()
            try:
                state.seen_headers.append(dict(self.headers))
                if parsed.path == "/slow":
                    time.sleep(int(qs.get("ms", ["500"])[0]) / 1000)
                    self._respond(200, {"slow": True})
                elif parsed.path.startswith("/status/"):
                    self._respond(int(parsed.path.split("/")[2]), {"ok": True})
                elif parsed.path == "/echo":
                    self._respond(
                        200,
                        {"echo": True},
                        extra_headers={
                            "Set-Cookie": "sid=super-secret",
                            "X-Secret-Token": "top-secret",
                            "Server": "BlazeTest/1.0",
                            "Location": "/somewhere",
                        },
                    )
                elif parsed.path == "/flaky":
                    with state.lock:
                        state.flaky_count += 1
                        attempt = state.flaky_count
                    if attempt % 3 != 0:
                        self._respond(503, {"attempt": attempt})
                    else:
                        self._respond(200, {"attempt": attempt})
                elif parsed.path == "/big":
                    self._respond(200, {"data": "x" * 50000})
                elif parsed.path == "/binary":
                    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/badutf8":
                    body = b"\xff\xfe\xfa\x01"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/charset":
                    body = b"caf\xe9"  # 'café' in latin-1
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=latin-1")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/empty":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._respond(404, {"error": "not found"})
            finally:
                state.leave()

        def do_POST(self):
            state.enter()
            try:
                raw = self._read_body()
                try:
                    data = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    data = {}
                with state.lock:
                    state.bodies.append(data)
                self._respond(200, {"received": data})
            finally:
                state.leave()

    return Handler


@pytest.fixture()
def server():
    state = _State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.state = state
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def server_url(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture()
def payload_file(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text(
        json.dumps(
            {
                "username": "{faker.user_name}",
                "n": "{int(min=1, max=9)}",
                "Authorization": "Bearer ${TEST_TOKEN}",
            }
        ),
        encoding="utf-8",
    )
    return path
