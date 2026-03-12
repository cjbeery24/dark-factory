"""Happy path tests for float multiplication."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from calculator import multiply


def test_multiply_floats():
    assert multiply(2.5, 4.0) == 10.0


def test_multiply_float_int():
    assert multiply(1.5, 2) == 3.0
