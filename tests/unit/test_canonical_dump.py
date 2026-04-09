"""Phase 2.1 — byte-equality golden test for canonical_dump."""

from pathlib import Path

import pytest

from darkfactory.architecture.environment import (
    BOOTSTRAP_ONLY_PATHS,
    Commands,
    EnvironmentContract,
    Layout,
    Runtime,
    ServerConfig,
    canonical_dump,
    validate_environment,
)


FIXTURE = Path(__file__).parent.parent / "fixtures" / "environment_canonical.toml"


def _golden_contract() -> EnvironmentContract:
    return EnvironmentContract(
        runtime=Runtime(
            language="typescript",
            package_manager="pnpm",
            language_version="22.11.0",
        ),
        commands=Commands(
            install="pnpm install --frozen-lockfile",
            server_start="pnpm --filter @budget-brain/api dev",
            server_health="curl -fsS http://localhost:8000/health",
            build="pnpm --filter @budget-brain/api build",
            lint="pnpm --filter @budget-brain/api lint",
            typecheck="pnpm --filter @budget-brain/api typecheck",
            test="pnpm --filter @budget-brain/api test",
        ),
        server=ServerConfig(port=8000, health_path="/health", boot_timeout_s=30),
        layout=Layout(
            source_dirs=["apps/api/src", "packages/shared/src"],
            test_dirs=["apps/api/tests"],
        ),
    )


def test_canonical_dump_byte_equal_to_fixture():
    actual = canonical_dump(_golden_contract())
    expected = FIXTURE.read_bytes()
    assert actual == expected


def test_canonical_dump_is_deterministic():
    a = canonical_dump(_golden_contract())
    b = canonical_dump(_golden_contract())
    assert a == b


def test_canonical_dump_ascii_only():
    out = canonical_dump(_golden_contract())
    out.decode("ascii")  # raises if non-ASCII


def test_canonical_dump_trailing_newline():
    out = canonical_dump(_golden_contract())
    assert out.endswith(b"\n")
    assert not out.endswith(b"\n\n")


def test_canonical_dump_empty_contract_is_stable():
    a = canonical_dump(EnvironmentContract())
    b = canonical_dump(EnvironmentContract())
    assert a == b
    assert a.endswith(b"\n")


def test_canonical_dump_escapes_quotes_and_backslashes():
    c = EnvironmentContract(
        commands=Commands(server_start='echo "hi" \\ bye'),
    )
    out = canonical_dump(c).decode("ascii")
    assert 'server_start = "echo \\"hi\\" \\\\ bye"' in out


def test_canonical_dump_escapes_non_ascii_as_unicode():
    c = EnvironmentContract(commands=Commands(test="café"))
    out = canonical_dump(c).decode("ascii")
    assert "\\u00E9" in out


# ----- validate_environment --------------------------------------------------


def test_validate_rejects_non_allowlist_paths():
    errors = validate_environment({"test.cmd": "pytest"}, "feature")
    assert len(errors) == 1
    assert errors[0].path == "test.cmd"
    assert "allowlist" in errors[0].reason


def test_validate_rejects_scenario_path():
    errors = validate_environment({"scenario.runner": "x"}, "bootstrap")
    assert len(errors) == 1


def test_validate_bootstrap_may_write_bootstrap_only_paths():
    errors = validate_environment(
        {"runtime.language": "python", "layout.source_dirs": ["src"]},
        "bootstrap",
    )
    assert errors == []


def test_validate_feature_rejects_bootstrap_only_paths():
    errors = validate_environment({"runtime.language": "python"}, "feature")
    assert len(errors) == 1
    assert "bootstrap-only" in errors[0].reason


def test_validate_bugfix_rejects_layout_source_dirs():
    errors = validate_environment({"layout.source_dirs": ["src"]}, "bugfix")
    assert len(errors) == 1


def test_validate_feature_accepts_command_amendments():
    errors = validate_environment(
        {"commands.server_start": "pnpm dev", "server.port": 8080},
        "feature",
    )
    assert errors == []


def test_bool_is_not_rendered_as_int():
    # bool is a subclass of int — _format_scalar must check it first.
    # The contract has no bool fields today, so smuggle one in via a list slot
    # by calling _format_scalar directly.
    from darkfactory.architecture.environment import _format_scalar
    assert _format_scalar(True) == "true"
    assert _format_scalar(False) == "false"
    # And verify that True does NOT serialize as "1".
    assert _format_scalar(True) != "1"


def test_format_scalar_negative_and_zero_int():
    from darkfactory.architecture.environment import _format_scalar
    assert _format_scalar(0) == "0"
    assert _format_scalar(-1) == "-1"
    assert _format_scalar(2**62) == str(2**62)


def test_empty_list_renders_as_bare_brackets():
    c = EnvironmentContract(layout=Layout(source_dirs=[], test_dirs=[]))
    out = canonical_dump(c).decode("ascii")
    assert "source_dirs = []" in out
    assert "test_dirs = []" in out


def test_empty_string_renders_as_empty_quotes():
    c = EnvironmentContract(commands=Commands(install=""))
    out = canonical_dump(c).decode("ascii")
    assert 'install = ""' in out


def test_canonical_dump_rejects_astral_codepoints_via_long_escape():
    # Codepoints > U+FFFF must use \UXXXXXXXX (8 hex digits), never \uXXXXX.
    c = EnvironmentContract(commands=Commands(test="\U0001F600"))
    out = canonical_dump(c).decode("ascii")
    assert "\\U0001F600" in out
    assert "\\u1F600" not in out  # the malformed 5-digit form must not appear


def test_canonical_dump_rejects_float():
    from darkfactory.architecture.environment import _format_scalar
    with pytest.raises(TypeError):
        _format_scalar(1.5)


def test_canonical_dump_rejects_none():
    from darkfactory.architecture.environment import _format_scalar
    with pytest.raises(TypeError):
        _format_scalar(None)


def test_validate_rejects_string_for_int_path():
    errors = validate_environment({"server.port": "8000"}, "feature")
    assert len(errors) == 1
    assert "expected int" in errors[0].reason


def test_validate_rejects_bool_for_int_path():
    errors = validate_environment({"server.port": True}, "feature")
    assert len(errors) == 1
    assert "expected int" in errors[0].reason


def test_validate_rejects_int_for_string_path():
    errors = validate_environment({"commands.test": 42}, "feature")
    assert len(errors) == 1
    assert "expected str" in errors[0].reason


def test_validate_rejects_non_string_list_element():
    errors = validate_environment({"layout.test_dirs": ["src", 1]}, "feature")
    assert len(errors) == 1


def test_validate_rejects_non_list_for_list_path():
    errors = validate_environment({"layout.test_dirs": "src"}, "feature")
    assert len(errors) == 1


def test_bootstrap_only_paths_are_subset_of_allowed():
    from darkfactory.architecture.environment import ALLOWED_PATHS
    assert BOOTSTRAP_ONLY_PATHS.issubset(ALLOWED_PATHS)
