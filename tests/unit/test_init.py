"""Tests for project initialization."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.errors import ConfigError
from darkfactory.setup.init import (
    _create_directories,
    _extract_existing_policy,
    _migrate_legacy_hints,
    _print_contracts_init_hints,
    _write_bootstrap_delta,
    _write_config,
    _write_project_hints,
    init_project,
)
from darkfactory.setup.detect import DetectionResult


class TestCreateDirectories:
    def test_creates_all_dirs(self, tmp_path: Path) -> None:
        _create_directories(tmp_path)
        expected = [
            ".dark-factory",
            ".dark-factory/specs",
            ".dark-factory/scenarios",
            ".dark-factory/reviews",
            ".dark-factory/reports",
            ".dark-factory/audit",
            ".dark-factory/state",
            ".dark-factory/plans",
        ]
        for d in expected:
            assert (tmp_path / d).is_dir(), f"{d} not created"

    def test_idempotent(self, tmp_path: Path) -> None:
        _create_directories(tmp_path)
        _create_directories(tmp_path)  # Should not raise.


class TestWriteConfig:
    def test_writes_language_only(self, tmp_path: Path) -> None:
        """Phase 3: config.toml has language only, no operational fields."""
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        detection = DetectionResult(
            language="python",
            source_dirs=["src/myapp"],
            test_dir="tests",
            test_cmd="pytest",
            lint_cmd="ruff check .",
        )
        _write_config(config_path, detection)

        content = config_path.read_text()
        assert 'language = "python"' in content
        # Operational fields are NOT in config.toml — they go to bootstrap delta.
        assert "source_dirs" not in content
        assert "test_dir" not in content
        assert "test_cmd" not in content
        assert "lint_cmd" not in content
        assert "[gates]" not in content

    def test_config_toml_has_no_gates_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        detection = DetectionResult(
            language="node",
            build_cmd="pnpm run build",
            test_cmd="pnpm test",
        )
        _write_config(config_path, detection)

        content = config_path.read_text()
        assert "[gates]" not in content
        assert "build_cmd" not in content


class TestWriteBootstrapDelta:
    def test_creates_bootstrap_delta(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="python",
            package_manager="pip",
            source_dirs=["src/myapp"],
            test_dir="tests",
            build_cmd="pip install -e . -q",
            lint_cmd="ruff check .",
            test_cmd="pytest",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        assert deltas_dir.is_dir()
        delta_files = list(deltas_dir.glob("*.toml"))
        assert len(delta_files) == 1
        assert "bootstrap-from-detection" in delta_files[0].name

    def test_bootstrap_delta_contains_detected_commands(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="node",
            package_manager="pnpm",
            build_cmd="pnpm run build",
            lint_cmd="pnpm -r lint",
            test_cmd="pnpm test",
            setup_cmd="pnpm exec prisma db push",
            server_cmd="pnpm run dev",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        delta_path = list(deltas_dir.glob("*.toml"))[0]
        raw = tomllib.loads(delta_path.read_text())
        ops = {op["path"]: op["value"] for op in raw["ops"]}

        assert ops["runtime.language"] == "node"
        assert ops["runtime.package_manager"] == "pnpm"
        assert ops["commands.build"] == "pnpm run build"
        assert ops["commands.lint"] == "pnpm -r lint"
        assert ops["commands.test"] == "pnpm test"
        assert ops["commands.setup"] == "pnpm exec prisma db push"
        assert ops["commands.server_start"] == "pnpm run dev"

    def test_bootstrap_delta_maps_install_cmd_to_commands_install(self, tmp_path: Path) -> None:
        # Sprint 22: python's dep-install rides install_cmd -> commands.install so
        # `dark-factory init` seeds it (the gate Install stage runs it before
        # Setup). Guards the parallel-table trap: _DETECT_TO_BOOTSTRAP must mirror
        # autodetect_delta's _DETECT_TO_ENV or a fresh init drops the install.
        detection = DetectionResult(
            language="python",
            install_cmd="pip install -e '.[dev]' -q --break-system-packages",
            test_cmd="pytest",
        )
        _write_bootstrap_delta(tmp_path, detection)
        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        delta_path = list(deltas_dir.glob("*.toml"))[0]
        ops = {op["path"]: op["value"] for op in tomllib.loads(delta_path.read_text())["ops"]}
        assert ops["commands.install"] == "pip install -e '.[dev]' -q --break-system-packages"
        assert "commands.build" not in ops  # python build is left to py_compile

    def test_bootstrap_delta_materializes_environment_toml(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="python",
            build_cmd="pip install -e . -q",
        )
        _write_bootstrap_delta(tmp_path, detection)

        env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
        assert env_path.exists()
        raw = tomllib.loads(env_path.read_text())
        assert raw["runtime"]["language"] == "python"
        assert raw["commands"]["build"] == "pip install -e . -q"

    def test_noop_when_no_detection(self, tmp_path: Path) -> None:
        detection = DetectionResult(language="unknown")
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        # No delta files should be written when language is unknown.
        if deltas_dir.exists():
            assert list(deltas_dir.glob("*.toml")) == []

    def test_bootstrap_delta_includes_source_dirs(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="python",
            source_dirs=["src/myapp", "src/lib"],
            test_dir="tests",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        delta_path = list(deltas_dir.glob("*.toml"))[0]
        raw = tomllib.loads(delta_path.read_text())
        ops = {op["path"]: op["value"] for op in raw["ops"]}

        assert ops["layout.source_dirs"] == ["src/myapp", "src/lib"]
        assert ops["layout.test_dirs"] == ["tests"]


    def test_reinit_skips_bootstrap_when_deltas_exist(self, tmp_path: Path) -> None:
        """Re-running init must not corrupt existing delta chain."""
        detection = DetectionResult(
            language="python",
            build_cmd="pip install -e . -q",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        count_after_first = len(list(deltas_dir.glob("*.toml")))

        # Second call with different detection — should be skipped.
        detection2 = DetectionResult(
            language="node",
            build_cmd="pnpm run build",
        )
        _write_bootstrap_delta(tmp_path, detection2)

        # No new delta file.
        assert len(list(deltas_dir.glob("*.toml"))) == count_after_first

    def test_rollback_on_materialize_failure(self, tmp_path: Path) -> None:
        detection = DetectionResult(language="python", build_cmd="make")
        with patch(
            "darkfactory.architecture.deltas.materialize",
            side_effect=RuntimeError("disk full"),
        ):
            with pytest.raises(RuntimeError, match="disk full"):
                _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        if deltas_dir.exists():
            assert list(deltas_dir.glob("*.toml")) == []


class TestInitProject:
    def test_non_interactive_python(self, tmp_path: Path) -> None:
        # Create a minimal Python project.
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")
        (tmp_path / "src" / "myapp").mkdir(parents=True)
        (tmp_path / "src" / "myapp" / "__init__.py").write_text("")
        (tmp_path / "tests").mkdir()

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True)

        assert result.success
        assert result.language == "python"
        assert (tmp_path / ".dark-factory" / "config.toml").exists()
        assert (tmp_path / ".dark-factory" / "specs").is_dir()
        # Bootstrap delta was created.
        assert (tmp_path / ".dark-factory" / "architecture" / "environment.toml").exists()

    def test_non_interactive_with_language_override(self, tmp_path: Path) -> None:
        # Empty project — force language.
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "app.py").write_text("")

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True, language="python")

        assert result.success
        assert result.language == "python"

    def test_fails_on_prereq_errors(self, tmp_path: Path) -> None:
        with patch("darkfactory.setup.init.check_prerequisites",
                    return_value=["Docker not installed"]):
            result = init_project(tmp_path, non_interactive=True, language="python")

        assert not result.success
        assert "Docker not installed" in result.errors

    def test_fails_on_unknown_language(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Hello")

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True)

        assert not result.success
        assert any("Could not detect" in e for e in result.errors)

    def test_new_project_config_toml_has_no_operational_fields(self, tmp_path: Path) -> None:
        """Phase 3: config.toml must not contain [gates], source_dirs, test_dir."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        (tmp_path / "src" / "app").mkdir(parents=True)
        (tmp_path / "src" / "app" / "__init__.py").write_text("")
        (tmp_path / "tests").mkdir()

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            init_project(tmp_path, non_interactive=True)

        config = (tmp_path / ".dark-factory" / "config.toml").read_text()
        assert "[gates]" not in config
        assert "source_dirs" not in config
        assert "test_dir" not in config
        assert "build_cmd" not in config


# ----- B2: project-hints.toml writer + legacy migration ---------------------


class TestWriteProjectHints:
    """``_write_project_hints`` — the B2 on-disk writer.

    Policy is *always* preserved (no ``preserve_policy`` switch): ``[policy]``
    is human-authored and the writer has no business deciding when to
    discard it. The scaffold is emitted only when no file exists.
    """

    def test_fresh_init_writes_detected_and_scaffold(self, tmp_path: Path) -> None:
        """Fresh init produces a file with populated [detected] and a
        *commented* [policy] scaffold (i.e. parses to empty policy).

        A5: adapter-activation is registry-driven via each adapter's
        ``detect()`` running against the real filesystem, so the
        fixture must supply the TypeScript signals (``package.json``)
        that ``TypescriptNodeAdapter.detect`` looks for.
        """
        (tmp_path / "package.json").write_text("{}")

        detection = DetectionResult(
            language="node",
            package_manager="pnpm",
            typescript=True,
            test_runner="vitest",
        )
        path = _write_project_hints(tmp_path, detection)

        assert path == tmp_path / ".dark-factory" / "project-hints.toml"
        raw = tomllib.loads(path.read_text())
        det = raw["detected"]
        assert det["language"] == "node"
        assert det["package_manager"] == "pnpm"
        assert det["typescript"] is True
        assert det["test_runner"] == "vitest"
        assert det["active_adapters"] == ["typescript-node"]
        assert det["schema_version"] == 1

        # Scaffold lines must be commented so the parsed [policy] is empty.
        assert raw.get("policy", {}) == {}
        # And the scaffold text must be physically present so users know it exists.
        text = path.read_text()
        assert "# [policy.migrations]" in text
        assert "# [policy.workspace]" in text
        assert "# [[policy.consistency_rules]]" in text

    def test_active_adapters_empty_without_typescript(self, tmp_path: Path) -> None:
        """Conservative registry mapping: only activate known adapters."""
        detection = DetectionResult(
            language="node",
            package_manager="npm",
            typescript=False,
        )
        _write_project_hints(tmp_path, detection)
        raw = tomllib.loads(
            (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        )
        assert raw["detected"]["active_adapters"] == []

    def test_active_adapters_name_sourced_from_adapter_class(self, tmp_path: Path) -> None:
        """active_adapters names must match the adapter's own `name` attribute
        so renames at the source of truth propagate here for free."""
        from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter

        (tmp_path / "package.json").write_text("{}")

        detection = DetectionResult(
            language="node", package_manager="pnpm", typescript=True,
        )
        _write_project_hints(tmp_path, detection)
        raw = tomllib.loads(
            (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        )
        assert raw["detected"]["active_adapters"] == [TypescriptNodeAdapter.name]

    def test_active_adapters_empty_for_python(self, tmp_path: Path) -> None:
        """No python adapter exists yet — active_adapters must stay empty."""
        detection = DetectionResult(language="python", package_manager="pip")
        _write_project_hints(tmp_path, detection)
        raw = tomllib.loads(
            (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        )
        assert raw["detected"]["active_adapters"] == []

    def test_round_trip_through_project_hints_loader(self, tmp_path: Path) -> None:
        """What _write_project_hints produces must be consumable by B1's loader."""
        from darkfactory.architecture.project_hints import load_project_hints

        (tmp_path / "package.json").write_text("{}")

        detection = DetectionResult(
            language="node", package_manager="pnpm",
            typescript=True, test_runner="vitest",
        )
        _write_project_hints(tmp_path, detection)
        hints = load_project_hints(tmp_path)
        assert hints.detected.language == "node"
        assert hints.detected.typescript is True
        assert hints.detected.test_runner == "vitest"
        assert hints.detected.active_adapters == ("typescript-node",)
        assert hints.policy.is_empty
        assert not hints.synthesized_from_legacy

    def test_preserve_policy_keeps_existing_rules(self, tmp_path: Path) -> None:
        """Re-running init must refresh [detected] without clobbering hand-authored [policy]."""
        hints_dir = tmp_path / ".dark-factory"
        hints_dir.mkdir()
        (hints_dir / "project-hints.toml").write_text("""\
[detected]
language = "node"
package_manager = "npm"

[policy.workspace]
scope = "apps/api"
banned_recursive = ["pnpm -r"]

[[policy.consistency_rules]]
name = "no-recursive"
spec_patterns = ["scoped to apps/api"]
forbidden_env = ["pnpm -r"]
env_paths = ["commands.*"]
description = "monorepo scope guard"
""")

        # Re-detection: pnpm takes over from npm.
        detection = DetectionResult(
            language="node", package_manager="pnpm", typescript=True, test_runner="vitest",
        )
        _write_project_hints(tmp_path, detection)

        raw = tomllib.loads((hints_dir / "project-hints.toml").read_text())
        # [detected] was refreshed.
        assert raw["detected"]["package_manager"] == "pnpm"
        assert raw["detected"]["typescript"] is True
        # [policy] was preserved verbatim (same values).
        assert raw["policy"]["workspace"]["scope"] == "apps/api"
        assert raw["policy"]["workspace"]["banned_recursive"] == ["pnpm -r"]
        rules = raw["policy"]["consistency_rules"]
        assert len(rules) == 1
        assert rules[0]["name"] == "no-recursive"
        assert rules[0]["description"] == "monorepo scope guard"

    def test_fresh_init_preserves_existing_policy(self, tmp_path: Path) -> None:
        """Re-running `init` (no flags) on a project with hand-authored [policy]
        must NOT silently overwrite it with the scaffold.

        Linus review Round 1 Issue #3 regression guard — the pre-fix code
        clobbered policy here because consent to overwrite config.toml was
        being treated as consent to overwrite project-hints.toml too.
        """
        hints_dir = tmp_path / ".dark-factory"
        hints_dir.mkdir()
        (hints_dir / "project-hints.toml").write_text("""\
[detected]
language = "node"

[policy.migrations]
mode = "cli-only"
command = "pnpm db:migrate"
""")

        detection = DetectionResult(language="node", typescript=True)
        _write_project_hints(tmp_path, detection)

        raw = tomllib.loads((hints_dir / "project-hints.toml").read_text())
        assert raw["policy"]["migrations"]["mode"] == "cli-only"
        assert raw["policy"]["migrations"]["command"] == "pnpm db:migrate"

    def test_preserve_policy_with_no_existing_file_emits_scaffold(
        self, tmp_path: Path,
    ) -> None:
        """On a project without project-hints.toml, the writer emits the scaffold."""
        detection = DetectionResult(language="python", package_manager="pip")
        _write_project_hints(tmp_path, detection)

        raw = tomllib.loads(
            (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        )
        assert raw.get("policy", {}) == {}
        text = (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        assert "# [policy.migrations]" in text

    def test_scaffold_re_emitted_when_existing_policy_is_empty(
        self, tmp_path: Path,
    ) -> None:
        """A file with only the commented scaffold parses to empty policy:
        preserve returns empty, and the writer re-emits a fresh scaffold
        (rather than duplicating the old commented block)."""
        from darkfactory.setup.init import _POLICY_SCAFFOLD
        hints_dir = tmp_path / ".dark-factory"
        hints_dir.mkdir()
        (hints_dir / "project-hints.toml").write_text(
            "[detected]\nlanguage = \"node\"\n\n" + _POLICY_SCAFFOLD
        )

        detection = DetectionResult(
            language="node", package_manager="pnpm", typescript=True,
        )
        _write_project_hints(tmp_path, detection)

        text = (hints_dir / "project-hints.toml").read_text()
        # Every scaffold marker appears exactly once — no duplication.
        assert text.count("# [policy.migrations]") == 1
        assert text.count("# [policy.workspace]") == 1
        assert text.count("# [[policy.consistency_rules]]") == 1

    def test_unparseable_existing_file_raises(self, tmp_path: Path) -> None:
        """A mangled existing file must NOT be silently overwritten —
        hand-authored [policy] rules would be lost. Raise ConfigError so
        the user can fix the TOML before retrying.

        Linus review Round 1 Issue #1 regression guard.
        """
        hints_dir = tmp_path / ".dark-factory"
        hints_dir.mkdir()
        (hints_dir / "project-hints.toml").write_text("this is [[ not toml")

        detection = DetectionResult(language="python", package_manager="pip")
        with pytest.raises(ConfigError, match="invalid TOML"):
            _write_project_hints(tmp_path, detection)

        # File is NOT touched — its broken content is still on disk for
        # the user to inspect and fix.
        assert (hints_dir / "project-hints.toml").read_text() == "this is [[ not toml"

    def test_rejects_symlink_target(self, tmp_path: Path) -> None:
        """Writing through a symlink would let an attacker redirect our
        writes. Refuse rather than realpath-follow.

        Linus review Round 1 Issue #2 regression guard.
        """
        target = tmp_path / "elsewhere.toml"
        target.write_text("# legitimate file the user cares about\n")
        hints_dir = tmp_path / ".dark-factory"
        hints_dir.mkdir()
        (hints_dir / "project-hints.toml").symlink_to(target)

        detection = DetectionResult(language="node", typescript=True)
        with pytest.raises(ConfigError, match="refusing to write through symlink"):
            _write_project_hints(tmp_path, detection)

        # Symlink target is untouched.
        assert target.read_text() == "# legitimate file the user cares about\n"

    def test_extract_existing_policy_empty_when_absent(self, tmp_path: Path) -> None:
        p = tmp_path / "project-hints.toml"
        p.write_text("[detected]\nlanguage = \"node\"\n")
        assert _extract_existing_policy(p) == ""

    def test_extract_existing_policy_raises_on_malformed_toml(
        self, tmp_path: Path,
    ) -> None:
        p = tmp_path / "project-hints.toml"
        p.write_text("not [[[ toml")
        with pytest.raises(ConfigError, match="invalid TOML"):
            _extract_existing_policy(p)

    def test_lockfiles_populated_from_detector(self, tmp_path: Path) -> None:
        """lockfiles flows through DetectionResult — not re-parsed here.

        Linus review Round 1 Issue #5 regression guard: the writer must
        not re-invoke detect_hints when DetectionResult already carries
        the answer.
        """
        detection = DetectionResult(
            language="node",
            package_manager="pnpm",
            lockfiles=["pnpm-lock.yaml"],
            workspace_layout="monorepo",
        )
        _write_project_hints(tmp_path, detection)
        raw = tomllib.loads(
            (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        )
        assert raw["detected"]["lockfiles"] == ["pnpm-lock.yaml"]
        assert raw["detected"]["workspace_layout"] == "monorepo"


class TestMigrateLegacyHints:
    """``_migrate_legacy_hints`` — hints.json deletion prompt."""

    def _setup_legacy(self, tmp_path: Path) -> Path:
        arch = tmp_path / ".dark-factory" / "architecture"
        arch.mkdir(parents=True)
        legacy = arch / "hints.json"
        legacy.write_text('{"language": "node"}')
        return legacy

    def test_no_legacy_file_is_noop(self, tmp_path: Path) -> None:
        _migrate_legacy_hints(tmp_path, non_interactive=True, yes=False)
        # no crash; nothing to assert beyond that.

    def test_yes_flag_deletes_legacy(self, tmp_path: Path) -> None:
        legacy = self._setup_legacy(tmp_path)
        _migrate_legacy_hints(tmp_path, non_interactive=True, yes=True)
        assert not legacy.exists()

    def test_non_interactive_without_yes_keeps_legacy(self, tmp_path: Path) -> None:
        """CI / scripted runs get the safe default: keep the file."""
        legacy = self._setup_legacy(tmp_path)
        _migrate_legacy_hints(tmp_path, non_interactive=True, yes=False)
        assert legacy.exists()

    def test_interactive_yes_deletes(self, tmp_path: Path) -> None:
        legacy = self._setup_legacy(tmp_path)
        with patch("darkfactory.setup.init.input", return_value="y"), \
             patch("sys.stdin.isatty", return_value=True):
            _migrate_legacy_hints(tmp_path, non_interactive=False, yes=False)
        assert not legacy.exists()

    def test_interactive_no_keeps(self, tmp_path: Path) -> None:
        legacy = self._setup_legacy(tmp_path)
        with patch("darkfactory.setup.init.input", return_value=""), \
             patch("sys.stdin.isatty", return_value=True):
            _migrate_legacy_hints(tmp_path, non_interactive=False, yes=False)
        assert legacy.exists()

    def test_environment_toml_is_never_touched(self, tmp_path: Path) -> None:
        """Migration scope is intentionally hints.json only."""
        arch = tmp_path / ".dark-factory" / "architecture"
        arch.mkdir(parents=True)
        env = arch / "environment.toml"
        env.write_text("[runtime]\nlanguage = \"node\"\n")
        legacy = arch / "hints.json"
        legacy.write_text('{"language": "node"}')

        _migrate_legacy_hints(tmp_path, non_interactive=True, yes=True)

        assert not legacy.exists()
        assert env.exists()  # preserved

    def test_rejects_symlink_legacy(self, tmp_path: Path) -> None:
        """A hints.json pointing at something the user cares about must
        not be silently unlinked.

        Linus review Round 1 Issue #2 regression guard.
        """
        target = tmp_path / "elsewhere.json"
        target.write_text('{"precious": true}')
        arch = tmp_path / ".dark-factory" / "architecture"
        arch.mkdir(parents=True)
        legacy = arch / "hints.json"
        legacy.symlink_to(target)

        with pytest.raises(ConfigError, match="refusing to delete through symlink"):
            _migrate_legacy_hints(tmp_path, non_interactive=True, yes=True)

        # Symlink and target both survive.
        assert legacy.is_symlink()
        assert target.read_text() == '{"precious": true}'

    def test_robust_delete_failure_logged_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If hints.json deletion fails (permission, etc.), we log and
        return — project-hints.toml was already written; aborting init
        over a failed cleanup step is the wrong trade-off.

        Linus review Round 1 Issue #7 regression guard.
        """
        import logging
        legacy = self._setup_legacy(tmp_path)

        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            with caplog.at_level(logging.WARNING, logger="darkfactory.setup.init"):
                _migrate_legacy_hints(tmp_path, non_interactive=True, yes=True)

        # Legacy file still present (unlink failed), but no crash.
        assert legacy.exists()
        assert any(
            "could not remove" in r.message for r in caplog.records
        )


class TestInitProjectB2:
    """Integration: init_project wires the new flags through correctly."""

    def _minimal_node_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            '{"name": "x", "devDependencies": {"typescript": "^5.0", "vitest": "^1.0"}}'
        )
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("")

    def test_fresh_init_creates_project_hints(self, tmp_path: Path) -> None:
        self._minimal_node_project(tmp_path)
        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True)

        assert result.success
        path = tmp_path / ".dark-factory" / "project-hints.toml"
        assert path.exists()
        raw = tomllib.loads(path.read_text())
        assert raw["detected"]["language"] == "node"
        assert raw["detected"]["typescript"] is True
        assert raw["detected"]["test_runner"] == "vitest"
        assert raw["detected"]["active_adapters"] == ["typescript-node"]

    def test_migrate_hints_deletes_legacy_with_yes(self, tmp_path: Path) -> None:
        self._minimal_node_project(tmp_path)
        # Pre-seed legacy file as though a prior dark-factory version left it.
        arch = tmp_path / ".dark-factory" / "architecture"
        arch.mkdir(parents=True)
        (arch / "hints.json").write_text('{"language": "node"}')

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(
                tmp_path, non_interactive=True, migrate_hints=True, yes=True,
            )

        assert result.success
        assert not (arch / "hints.json").exists()
        # New file is present and loadable.
        from darkfactory.architecture.project_hints import load_project_hints
        hints = load_project_hints(tmp_path)
        assert hints.detected.language == "node"
        assert not hints.synthesized_from_legacy

    def test_update_preserves_policy(self, tmp_path: Path) -> None:
        self._minimal_node_project(tmp_path)
        # First init.
        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            init_project(tmp_path, non_interactive=True)

        # Hand-author a [policy] block.
        hints_path = tmp_path / ".dark-factory" / "project-hints.toml"
        current = hints_path.read_text()
        # Splice real policy at the end (replacing scaffold).
        authored = current + """

[policy.migrations]
mode = "cli-only"
command = "pnpm db:migrate"
"""
        hints_path.write_text(authored)

        # Re-init with --update; [policy] must survive.
        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            init_project(tmp_path, non_interactive=True, update=True)

        raw = tomllib.loads(hints_path.read_text())
        assert raw["policy"]["migrations"]["mode"] == "cli-only"
        assert raw["policy"]["migrations"]["command"] == "pnpm db:migrate"

    def test_migration_consolidated_facts_win_over_legacy(self, tmp_path: Path) -> None:
        """When the legacy hints.json disagrees with fresh detection, the
        new project-hints.toml reflects the filesystem (authoritative),
        not the stale legacy content.

        Proves the migration actually re-detects rather than pass-through
        copying from hints.json.
        """
        self._minimal_node_project(tmp_path)
        arch = tmp_path / ".dark-factory" / "architecture"
        arch.mkdir(parents=True)
        # Legacy lies: says "python" despite filesystem being Node.
        (arch / "hints.json").write_text(
            '{"language": "python", "package_manager": "poetry"}'
        )

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(
                tmp_path, non_interactive=True, migrate_hints=True, yes=True,
            )

        assert result.success
        raw = tomllib.loads(
            (tmp_path / ".dark-factory" / "project-hints.toml").read_text()
        )
        # Fresh detection wins.
        assert raw["detected"]["language"] == "node"
        assert raw["detected"]["package_manager"] == "pnpm"

    def test_malformed_hints_surfaces_error_in_result(self, tmp_path: Path) -> None:
        """A malformed pre-existing project-hints.toml surfaces as an
        error in InitResult, does NOT silently overwrite hand-authored
        content, and marks the init as failed."""
        self._minimal_node_project(tmp_path)
        hints_dir = tmp_path / ".dark-factory"
        hints_dir.mkdir()
        (hints_dir / "project-hints.toml").write_text("not [[ valid toml")

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True, update=True)

        assert not result.success
        assert any("invalid TOML" in e for e in result.errors)
        # File content preserved — user can fix and retry.
        assert (
            (hints_dir / "project-hints.toml").read_text() == "not [[ valid toml"
        )


class TestContractsInitHints:
    """`_print_contracts_init_hints` surfaces a per-adapter setup nudge
    when active adapters ship project-helper starters but the project's
    contracts dir hasn't been init'd yet. Closes the discoverability
    gap the framework had through Phase 4 — developer hits the missing
    dir on first pipeline run rather than at init time.

    Pinned behavior:
    - Hint appears for postgres-active project without contracts/postgres/
    - Hint is suppressed when contracts/postgres/ already exists (re-init noise)
    - Hint is suppressed for adapters with no project-helper starters
    - No output (clean stdout) when nothing needs init'ing
    """

    def _postgres_project(self, tmp_path: Path) -> None:
        """A node project with a .sql file so the postgres adapter's
        ``detect()`` returns True. The postgres detector probes for
        SQL files, docker-compose postgres services, or connection
        strings — package.json deps alone are NOT a signal."""
        (tmp_path / "package.json").write_text(
            '{"name": "x", "devDependencies": {"typescript": "^5.0", '
            '"vitest": "^1.0"}}'
        )
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("")
        (tmp_path / "src" / "schema.sql").write_text(
            "CREATE TABLE users (id uuid PRIMARY KEY);\n"
        )

    def test_hint_appears_for_postgres_active_project(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._postgres_project(tmp_path)
        _print_contracts_init_hints(tmp_path)
        captured = capsys.readouterr()
        assert "dark-factory contracts init postgres" in captured.out
        assert "Next steps" in captured.out

    def test_hint_suppressed_when_contracts_dir_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._postgres_project(tmp_path)
        # Pre-create the contracts dir to simulate a prior init.
        (tmp_path / ".dark-factory" / "contracts" / "postgres").mkdir(
            parents=True,
        )
        _print_contracts_init_hints(tmp_path)
        captured = capsys.readouterr()
        assert captured.out == "", (
            "hint must be suppressed when the contracts dir already "
            "exists — re-running init on a populated project shouldn't "
            "re-prompt for a step that's already been done"
        )

    def test_hint_suppressed_for_typescript_only_project(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Same _minimal_node_project shape from earlier in the file —
        # no postgres deps. typescript-node activates but it has no
        # project-helper starters today.
        (tmp_path / "package.json").write_text(
            '{"name": "x", "devDependencies": {"typescript": "^5.0", '
            '"vitest": "^1.0"}}'
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("")
        _print_contracts_init_hints(tmp_path)
        captured = capsys.readouterr()
        assert captured.out == "", (
            "typescript-node has no project-helper starters; the hint "
            "must be empty even though typescript-node IS in active_adapters"
        )

    def test_hint_lists_only_init_eligible_active_adapters(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Mixed-stack project: typescript-node + postgres both active.
        Hint must include postgres (has starters) but NOT typescript-node
        (no starters today)."""
        self._postgres_project(tmp_path)
        _print_contracts_init_hints(tmp_path)
        captured = capsys.readouterr()
        assert "postgres" in captured.out
        assert "typescript-node" not in captured.out
