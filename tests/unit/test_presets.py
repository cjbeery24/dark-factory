"""Tests for deployment presets."""

from __future__ import annotations

from pathlib import Path

from darkfactory.deployment.presets import apply_preset
from darkfactory.models.config import PipelineConfig


class TestDockerComposePreset:
    def test_fills_empty_fields(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            deploy_preset="docker-compose",
            deploy_base_url="http://localhost:3000",
        )
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
        apply_preset(config, str(tmp_path))

        assert config.deploy_compose_file == "docker-compose.yml"
        assert "down --remove-orphans" in config.deploy_teardown_cmd
        assert config.health_check_url == "http://localhost:3000/health"

    def test_does_not_overwrite_explicit_fields(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            deploy_preset="docker-compose",
            deploy_compose_file="custom-compose.yml",
            deploy_teardown_cmd="custom teardown",
            health_check_url="http://custom:9090/healthz",
        )
        apply_preset(config, str(tmp_path))

        assert config.deploy_compose_file == "custom-compose.yml"
        assert config.deploy_teardown_cmd == "custom teardown"
        assert config.health_check_url == "http://custom:9090/healthz"

    def test_finds_compose_yaml_variant(self, tmp_path: Path) -> None:
        config = PipelineConfig(deploy_preset="docker-compose")
        (tmp_path / "compose.yaml").write_text("version: '3'\n")
        apply_preset(config, str(tmp_path))
        assert config.deploy_compose_file == "compose.yaml"

    def test_picks_up_deploy_scripts(self, tmp_path: Path) -> None:
        config = PipelineConfig(deploy_preset="docker-compose")
        scripts_dir = tmp_path / "scripts" / "deploy" / "docker-compose"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "deploy.sh").write_text("#!/bin/bash\necho deploy")
        (scripts_dir / "rollback.sh").write_text("#!/bin/bash\necho rollback")

        apply_preset(config, str(tmp_path))

        assert config.deploy_cmd == str(scripts_dir / "deploy.sh")
        assert config.deploy_rollback_cmd == str(scripts_dir / "rollback.sh")

    def test_default_health_url_when_no_base(self, tmp_path: Path) -> None:
        config = PipelineConfig(deploy_preset="docker-compose")
        apply_preset(config, str(tmp_path))
        assert config.health_check_url == "http://localhost:8080/health"


class TestK8sArgoPreset:
    def test_fills_health_check(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            deploy_preset="k8s-argo",
            deploy_base_url="http://my-service:3000",
        )
        apply_preset(config, str(tmp_path))
        assert config.health_check_url == "http://my-service:3000/health"

    def test_picks_up_scripts(self, tmp_path: Path) -> None:
        config = PipelineConfig(deploy_preset="k8s-argo")
        scripts_dir = tmp_path / "scripts" / "deploy" / "k8s-argo"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "deploy.sh").write_text("#!/bin/bash")
        apply_preset(config, str(tmp_path))
        assert config.deploy_cmd == str(scripts_dir / "deploy.sh")


class TestNonePreset:
    def test_noop(self, tmp_path: Path) -> None:
        config = PipelineConfig(deploy_preset="none")
        apply_preset(config, str(tmp_path))
        assert config.deploy_cmd == ""
        assert config.deploy_compose_file == ""

    def test_unknown_preset_is_noop(self, tmp_path: Path) -> None:
        config = PipelineConfig.__new__(PipelineConfig)
        # Bypass validation to test unknown preset handling.
        object.__setattr__(config, "deploy_preset", "unknown-thing")
        object.__setattr__(config, "deploy_cmd", "")
        object.__setattr__(config, "deploy_compose_file", "")
        apply_preset(config, str(tmp_path))
        assert config.deploy_cmd == ""
