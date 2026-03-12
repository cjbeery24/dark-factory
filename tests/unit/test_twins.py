"""Tests for digital twin recorder/replayer (Phase 7D)."""

import hashlib
import json
import tempfile
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

from darkfactory.twins.manager import TwinManager
from darkfactory.twins.models import RecordedInteraction, TwinSpec
from darkfactory.twins.recorder import start_recorder
from darkfactory.twins.replayer import load_recordings, start_replayer, _build_index


class _EchoHandler(BaseHTTPRequestHandler):
    """Simple echo server for testing."""
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"path": self.path, "method": "GET"}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"path": self.path, "method": "POST", "echo": data.decode()}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class TestRecorderReplayerRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        # Start echo server.
        self.echo_server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
        self.echo_port = self.echo_server.server_address[1]
        self.echo_thread = Thread(target=self.echo_server.serve_forever, daemon=True)
        self.echo_thread.start()

    def tearDown(self) -> None:
        self.echo_server.shutdown()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_replay(self) -> None:
        record_file = Path(self.tmpdir) / "test.jsonl"
        upstream = f"http://127.0.0.1:{self.echo_port}"

        # Record.
        rec_server, rec_port = start_recorder(upstream, record_file)
        try:
            url = f"http://127.0.0.1:{rec_port}/api/test"
            with urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
            self.assertEqual(data["path"], "/api/test")
            self.assertEqual(data["method"], "GET")
        finally:
            rec_server.shutdown()

        # Verify recording was written.
        self.assertTrue(record_file.exists())
        recordings = load_recordings(record_file)
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0].method, "GET")
        self.assertEqual(recordings[0].path, "/api/test")

        # Replay.
        rep_server, rep_port = start_replayer(record_file)
        try:
            url = f"http://127.0.0.1:{rep_port}/api/test"
            with urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
            self.assertEqual(data["path"], "/api/test")
        finally:
            rep_server.shutdown()

    def test_unmatched_request_returns_404(self) -> None:
        record_file = Path(self.tmpdir) / "empty.jsonl"
        record_file.touch()

        server, port = start_replayer(record_file)
        try:
            url = f"http://127.0.0.1:{port}/nonexistent"
            req = Request(url)
            from urllib.error import HTTPError
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            server.shutdown()


class TestBuildIndex(unittest.TestCase):
    def test_last_write_wins(self) -> None:
        interactions = [
            RecordedInteraction("GET", "/a", "h1", 200, "first"),
            RecordedInteraction("GET", "/a", "h1", 200, "second"),
        ]
        index = _build_index(interactions)
        self.assertEqual(index[("GET", "/a", "h1")].response_body, "second")


class TestTwinManager(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.echo_server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
        self.echo_port = self.echo_server.server_address[1]
        self.echo_thread = Thread(target=self.echo_server.serve_forever, daemon=True)
        self.echo_thread.start()

    def tearDown(self) -> None:
        self.echo_server.shutdown()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_manager_record_and_health(self) -> None:
        manager = TwinManager(Path(self.tmpdir))
        specs = [TwinSpec(name="echo", upstream=f"http://127.0.0.1:{self.echo_port}")]
        ports = manager.start_recording(specs)
        self.assertIn("echo", ports)

        health = manager.health()
        self.assertTrue(health["echo"])

        manager.stop()


if __name__ == "__main__":
    unittest.main()
