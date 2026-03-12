"""Error condition tests for type validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from calculator import multiply
import pytest


def test_multiply_string_raises():
    with pytest.raises(TypeError):
        multiply("a", 3)


def test_multiply_none_raises():
    with pytest.raises(TypeError):
        multiply(None, 3)
