"""Tests for the calculator module.

This file is a test fixture consumed by Dark Factory integration tests.
The sys.path manipulation is intentional — it mirrors how a real user project
would be structured when running inside a Docker container without an editable
pip install.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from calculator import add, subtract, multiply


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply_basic():
    assert multiply(3, 4) == 12


def test_multiply_zero():
    assert multiply(0, 99) == 0


def test_multiply_negative():
    assert multiply(-2, 3) == -6
