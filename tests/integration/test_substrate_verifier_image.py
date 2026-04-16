"""Substrate verifier image smoke test.

Locks the invariant the verifier dispatcher depends on: the
``dark-factory-python`` image must contain every third-party runtime
dependency that ``darkfactory.adapters`` transitively imports.

The verifier mounts the host ``darkfactory/`` package read-only at
``/opt/dark-factory/darkfactory`` and runs with ``--no-deps`` pip
installs, so any new import in an adapter module (or in its transitive
import chain through ``darkfactory.spec.*`` /
``darkfactory.architecture.*``) that is not pinned in
``docker/lockfiles/python-tools/requirements.txt`` will break
substrate verification at container-start. Catching that drift in CI
beats catching it as a ``substrate-verifier-crashed`` finding deep in
a pipeline run, where the truncated stderr hides the actual
ModuleNotFoundError.
"""
from __future__ import annotations

import subprocess

import pytest

from darkfactory.docker.containers import execute_container
from darkfactory.docker.mounts import build_darkfactory_mount

_IMAGE = "dark-factory-python"


def _docker_available() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _image_exists(image: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_skip_no_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker daemon not available"
)
_skip_no_image = pytest.mark.skipif(
    not _image_exists(_IMAGE),
    reason=(
        f"Docker image {_IMAGE} not built — "
        f"run `bash docker/build-images.sh python` first"
    ),
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    _skip_no_docker,
    _skip_no_image,
]


class TestVerifierImageImports:
    """Verifier image must import the full adapter registry chain."""

    def test_adapters_package_imports(self) -> None:
        """``from darkfactory.adapters import get_adapter_class`` must succeed.

        Replays the exact invocation shape ``_build_verifier_docker_args``
        emits for a real run — same entrypoint, PYTHONPATH, and
        darkfactory bind mount — minus the spec/manifest mounts that
        aren't needed for the import smoke. If this fails with
        ``ModuleNotFoundError`` for a new third-party package, add it
        to ``docker/lockfiles/python-tools/requirements.txt`` (via
        ``docker/regenerate-lockfiles.sh``) and rebuild the image.
        """
        result = execute_container(
            [
                "--entrypoint", "python3",
                "-e", "PYTHONPATH=/opt/dark-factory",
                *build_darkfactory_mount(),
            ],
            _IMAGE,
            [
                "-c",
                (
                    "from darkfactory.adapters import get_adapter_class; "
                    "from darkfactory.adapters.base import Finding; "
                    "print('ok')"
                ),
            ],
            timeout=30,
        )
        assert result.exit_code == 0, (
            f"verifier image failed to import darkfactory.adapters: "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert "ok" in result.stdout

    def test_substrate_verifier_entry_module_loads(self) -> None:
        """The entry script must at least parse and run past imports.

        We invoke ``-m darkfactory.pipeline.substrate_verifier_entry``
        with ``--help`` so argparse exits 0 *after* the module body
        (including the ``from darkfactory.adapters import ...`` at
        line 52) has executed. If the imports fail, Python exits with
        a non-zero status before argparse sees anything. This closes
        the gap between "adapters package can be imported standalone"
        and "the entry script can actually start up."
        """
        result = execute_container(
            [
                "--entrypoint", "python3",
                "-e", "PYTHONPATH=/opt/dark-factory",
                *build_darkfactory_mount(),
            ],
            _IMAGE,
            [
                "-m", "darkfactory.pipeline.substrate_verifier_entry",
                "--help",
            ],
            timeout=30,
        )
        assert result.exit_code == 0, (
            f"substrate_verifier_entry failed to start: "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
