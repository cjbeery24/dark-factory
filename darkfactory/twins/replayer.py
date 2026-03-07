"""Deterministic replay server — serves recorded responses by method+path+body_hash."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from darkfactory.twins.models import RecordedInteraction

log = logging.getLogger(__name__)


def load_recordings(record_file: Path) -> list[RecordedInteraction]:
    """Load recorded interactions from a JSONL file."""
    if not record_file.exists():
        return []

    interactions = []
    with record_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            interactions.append(RecordedInteraction(
                method=data["method"],
                path=data["path"],
                body_hash=data["body_hash"],
                status_code=data["status_code"],
                response_body=data["response_body"],
                request_headers=data.get("request_headers", {}),
                response_headers=data.get("response_headers", {}),
            ))
    return interactions


def _build_index(
    interactions: list[RecordedInteraction],
) -> dict[tuple[str, str, str], RecordedInteraction]:
    """Build lookup index: (method, path, body_hash) -> interaction.

    Later entries override earlier ones (last-write-wins).
    """
    index: dict[tuple[str, str, str], RecordedInteraction] = {}
    for i in interactions:
        index[(i.method, i.path, i.body_hash)] = i
    return index


class ReplayHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves recorded responses."""

    index: dict[tuple[str, str, str], RecordedInteraction] = {}

    def do_GET(self) -> None:
        self._replay("GET")

    def do_POST(self) -> None:
        self._replay("POST")

    def do_PUT(self) -> None:
        self._replay("PUT")

    def do_DELETE(self) -> None:
        self._replay("DELETE")

    def do_PATCH(self) -> None:
        self._replay("PATCH")

    def _replay(self, method: str) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        body_hash = hashlib.sha256(body).hexdigest()[:16]

        key = (method, self.path, body_hash)
        interaction = self.index.get(key)

        # Fallback: try matching without body hash (for GET requests).
        if interaction is None:
            fallback_key = (method, self.path, hashlib.sha256(b"").hexdigest()[:16])
            interaction = self.index.get(fallback_key)

        if interaction is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            body_resp = json.dumps({"error": "no recorded response", "key": list(key)})
            self.send_header("Content-Length", str(len(body_resp)))
            self.end_headers()
            self.wfile.write(body_resp.encode())
            return

        self.send_response(interaction.status_code)
        for k, v in interaction.response_headers.items():
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                self.send_header(k, v)
        encoded = interaction.response_body.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        log.debug("twin-replayer: %s", format % args)


def start_replayer(
    record_file: Path,
    port: int = 0,
) -> tuple[HTTPServer, int]:
    """Start a replay server from a JSONL recording file.

    Returns (server, actual_port). The server runs in a daemon thread.
    """
    interactions = load_recordings(record_file)
    index = _build_index(interactions)

    handler = type(
        "BoundReplayHandler",
        (ReplayHandler,),
        {"index": index},
    )

    server = HTTPServer(("127.0.0.1", port), handler)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    log.info("twin replayer started: 127.0.0.1:%d (%d recordings)",
             actual_port, len(interactions))
    return server, actual_port
