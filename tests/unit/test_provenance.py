"""Tests for provenance tracking across config layers (Phase 1).

Validates that PipelineConfig records which layer set each field:
config.toml, environment.toml, or auto-detect.
"""

import pytest

from darkfactory.architecture.environment import Commands, EnvironmentContract, Layout
from darkfactory.architecture.runtime import (
    _reset_cache,
    refresh_config_from_environment,
)
from darkfactory.config.loader import load_config
from darkfactory.models.config import PipelineConfig


@pytest.fixture(autouse=True)
def _clean_cache():
    _reset_cache()
    yield
    _reset_cache()


# --- PipelineConfig provenance basics ---


def test_provenance_default_is_empty():
    config = PipelineConfig()
    assert config._provenance == {}
    assert config.get_provenance("build_cmd") == "default"


def test_provenance_set_and_get():
    config = PipelineConfig()
    config.set_provenance("build_cmd", "environment.toml")
    assert config.get_provenance("build_cmd") == "environment.toml"
    assert config.get_provenance("lint_cmd") == "default"


def test_provenance_does_not_affect_equality():
    a = PipelineConfig()
    b = PipelineConfig()
    a.set_provenance("build_cmd", "environment.toml")
    assert a == b  # provenance excluded from comparison


def test_provenance_does_not_appear_in_repr():
    config = PipelineConfig()
    config.set_provenance("build_cmd", "environment.toml")
    assert "_provenance" not in repr(config)


# --- Config loader provenance ---


def test_loaded_config_has_provenance_for_set_fields(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[core]\nlanguage = "python"\n\n[gates]\nbuild_cmd = "make"\n')
    config = load_config(toml)
    assert config.get_provenance("language") == "config.toml:[core]"
    assert config.get_provenance("build_cmd") == "config.toml:[gates]"


def test_unset_fields_have_default_provenance(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[core]\nlanguage = "node"\n')
    config = load_config(toml)
    assert config.get_provenance("build_cmd") == "default"
    assert config.get_provenance("lint_cmd") == "default"


# --- Environment overlay provenance ---


def test_refresh_sets_provenance_on_overlaid_fields():
    config = PipelineConfig(test_cmd="OLD", build_cmd="OLD")
    config.set_provenance("test_cmd", "config.toml:[gates]")
    config.set_provenance("build_cmd", "config.toml:[gates]")
    env = EnvironmentContract(commands=Commands(test="NEW", build="NEW2"))
    refresh_config_from_environment(config, env)
    assert config.get_provenance("test_cmd") == "environment.toml"
    assert config.get_provenance("build_cmd") == "environment.toml"


def test_refresh_does_not_set_provenance_on_skipped_fields():
    config = PipelineConfig(test_cmd="KEEP")
    config.set_provenance("test_cmd", "config.toml:[gates]")
    env = EnvironmentContract(commands=Commands())  # all empty
    refresh_config_from_environment(config, env)
    assert config.get_provenance("test_cmd") == "config.toml:[gates]"


def test_refresh_sets_provenance_on_source_dirs():
    config = PipelineConfig(source_dirs=["src"])
    env = EnvironmentContract(layout=Layout(source_dirs=["apps/api/src"]))
    refresh_config_from_environment(config, env)
    assert config.get_provenance("source_dirs") == "environment.toml"


def test_refresh_sets_provenance_on_services():
    from darkfactory.architecture.environment import ServiceEntry
    config = PipelineConfig()
    env = EnvironmentContract(
        services={"postgres": ServiceEntry(image="postgres:16-alpine")}
    )
    refresh_config_from_environment(config, env)
    assert config.get_provenance("services") == "environment.toml"


# --- dataclasses.replace safety ---


def test_provenance_survives_dataclasses_replace():
    """dataclasses.replace() must not share the _provenance dict."""
    import dataclasses
    original = PipelineConfig()
    original.set_provenance("build_cmd", "config.toml:[gates]")
    copy = dataclasses.replace(original, max_iterations=5)
    # Copy inherits provenance from original
    assert copy.get_provenance("build_cmd") == "config.toml:[gates]"
    # But the dicts are independent — mutating one doesn't affect the other
    copy.set_provenance("build_cmd", "environment.toml")
    assert original.get_provenance("build_cmd") == "config.toml:[gates]"
    assert copy.get_provenance("build_cmd") == "environment.toml"
