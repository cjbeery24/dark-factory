"""Contract tests for multiply function."""
import sys
from pathlib import Path

# Add src to path so we can import calculator
sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from calculator import multiply


def test_multiply_integers():
    assert multiply(3, 4) == 12


def test_multiply_negative():
    assert multiply(-2, 5) == -10


def test_multiply_negative_both():
    assert multiply(-3, -4) == 12
