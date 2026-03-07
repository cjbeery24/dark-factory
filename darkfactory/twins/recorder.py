"""HTTP proxy recorder — captures request/response pairs to JSONL."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

from darkfactory.twins.models import RecordedInteraction

log = logging.getLogger(__name__)


class RecordingHandler(BaseHTTPRequestHandler):
    """HTTP handler that proxies requests and records interactions."""

    upstream: str = ""
    record_file: Path = Path("/dev/null")
    _lock: threading.Lock = threading.Lock()

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")

    def do_PATCH(self) -> None:
        self._proxy("PATCH")

    def _proxy(self, method: str) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        body_hash = hashlib.sha256(body).hexdigest()[:16]

        url = f"{self.upstream}{self.path}"
        # Pass body for methods that had Content-Length, None otherwise.
        req_data = body if content_length > 0 else None
        req = Request(url, data=req_data, method=method)

        # Forward headers (skip Host).
        for key, value in self.headers.items():
            if key.lower() not in ("host", "content-length"):
                req.add_header(key, value)

        try:
            with urlopen(req, timeout=30) as resp:
                status = resp.status
                resp_body = resp.read().decode("utf-8", errors="replace")
                resp_headers = dict(resp.headers)
        except URLError as e:
            status = 502
            resp_body = str(e)
            resp_headers = {}

        # Record interaction.
        interaction = RecordedInteraction(
            method=method,
            path=self.path,
            body_hash=body_hash,
            status_code=status,
            response_body=resp_body,
            request_headers={k: v for k, v in self.headers.items()
                             if k.lower() not in ("host",)},
            response_headers=resp_headers,
        )
        self._write_record(interaction)

        # Send response.
        self.send_response(status)
        for k, v in resp_headers.items():
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                self.send_header(k, v)
        encoded = resp_body.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_record(self, interaction: RecordedInteraction) -> None:
        record = {
            "method": interaction.method,
            "path": interaction.path,
            "body_hash": interaction.body_hash,
            "status_code": interaction.status_code,
            "response_body": interaction.response_body,
            "request_headers": interaction.request_headers,
            "response_headers": interaction.response_headers,
        }
        with self._lock:
            with self.record_file.open("a") as f:
                f.write(json.dumps(record) + "\n")

    def log_message(self, format: str, *args: object) -> None:
        log.debug("twin-recorder: %s", format % args)


def start_recorder(
    upstream: str,
    record_file: Path,
    port: int = 0,
) -> tuple[HTTPServer, int]:
    """Start a recording proxy server.

    Returns (server, actual_port). The server runs in a daemon thread.
    """
    record_file.parent.mkdir(parents=True, exist_ok=True)

    handler = type(
        "BoundHandler",
        (RecordingHandler,),
        {
            "upstream": upstream,
            "record_file": record_file,
            "_lock": threading.Lock(),
        },
    )

    server = HTTPServer(("127.0.0.1", port), handler)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    log.info("twin recorder started: %s -> 127.0.0.1:%d (recording to %s)",
             upstream, actual_port, record_file)
    return server, actual_port
