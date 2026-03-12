"""Edge case tests for zero multiplication."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from calculator import multiply


def test_multiply_zero_left():
    assert multiply(0, 99) == 0


def test_multiply_zero_right():
    assert multiply(99, 0) == 0


def test_multiply_zero_both():
    assert multiply(0, 0) == 0
