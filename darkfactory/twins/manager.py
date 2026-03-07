"""TwinManager — lifecycle management for digital twin services."""

from __future__ import annotations

import logging
import socket
from http.server import HTTPServer
from pathlib import Path

from darkfactory.twins.models import TwinSpec
from darkfactory.twins.recorder import start_recorder
from darkfactory.twins.replayer import start_replayer

log = logging.getLogger(__name__)


class TwinManager:
    """Manages lifecycle of digital twin recorder/replayer servers."""

    def __init__(self, record_dir: Path) -> None:
        self.record_dir = record_dir
        self._servers: list[tuple[str, HTTPServer]] = []
        self._ports: dict[str, int] = {}

    def start_recording(self, specs: list[TwinSpec]) -> dict[str, int]:
        """Start recording proxies for all twin specs.

        Returns mapping of twin name -> local port.
        """
        for spec in specs:
            record_file = self.record_dir / f"{spec.name}.jsonl"
            server, port = start_recorder(spec.upstream, record_file, port=spec.port)
            self._servers.append((spec.name, server))
            self._ports[spec.name] = port
            log.info("twin %s recording: %s -> :%d", spec.name, spec.upstream, port)
        return dict(self._ports)

    def start_replaying(self, specs: list[TwinSpec]) -> dict[str, int]:
        """Start replay servers from existing recordings.

        Returns mapping of twin name -> local port.
        """
        for spec in specs:
            record_file = self.record_dir / f"{spec.name}.jsonl"
            if not record_file.exists():
                log.warning("no recording for twin %s, skipping", spec.name)
                continue
            server, port = start_replayer(record_file, port=spec.port)
            self._servers.append((spec.name, server))
            self._ports[spec.name] = port
            log.info("twin %s replaying on :%d", spec.name, port)
        return dict(self._ports)

    def stop(self) -> None:
        """Stop all running twin servers."""
        for name, server in self._servers:
            try:
                server.shutdown()
                log.info("twin %s stopped", name)
            except Exception as e:
                log.warning("error stopping twin %s: %s", name, e)
        self._servers.clear()
        self._ports.clear()

    def health(self) -> dict[str, bool]:
        """Check if all twin servers are running."""
        result: dict[str, bool] = {}
        for name, port in self._ports.items():
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    result[name] = True
            except (OSError, ConnectionRefusedError):
                result[name] = False
        return result
