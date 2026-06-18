"""Project initialization — zero-config bootstrap for dark-factory.

Creates .dark-factory/ directory structure, detects project characteristics,
generates a working config.toml, and validates prerequisites.
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from darkfactory.errors import ConfigError
from darkfactory.setup.detect import DetectionResult, detect_project

log = logging.getLogger(__name__)


@dataclass
class InitResult:
    """Result of project initialization."""

    success: bool = False
    config_path: str = ""
    language: str = ""
    source_dirs: list[str] | None = None
    test_dir: str = ""
    framework: str = ""
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.source_dirs is None:
            self.source_dirs = []
        if self.errors is None:
            self.errors = []


# Directories created during init.
_REQUIRED_DIRS = [
    ".dark-factory",
    ".dark-factory/specs",
    ".dark-factory/scenarios",
    ".dark-factory/reviews",
    ".dark-factory/reports",
    ".dark-factory/audit",
    ".dark-factory/state",
    ".dark-factory/plans",
    ".dark-factory/shipped-scenarios",
]


def init_project(
    project_dir: Path,
    *,
    non_interactive: bool = False,
    language: str = "",
    update: bool = False,
    migrate_hints: bool = False,
    yes: bool = False,
) -> InitResult:
    """Initialize a Dark Factory project.

    Creates directory structure, detects project characteristics,
    generates config.toml, and validates prerequisites.

    Args:
        project_dir: Project root directory.
        non_interactive: Skip interactive prompts, use detected defaults.
        language: Override language detection (for --non-interactive).
        update: Refresh framework files + re-detect into project-hints.toml
            [detected], preserving [policy].
        migrate_hints: Consolidate legacy hints.json into project-hints.toml
            and (if confirmed) delete the old file. Implies freshened
            re-detection like --update.
        yes: Assume yes for migration prompts (CI-safe; no interactive input).

    Returns:
        InitResult with success status and detected characteristics.
    """
    result = InitResult()

    # Step 1: Validate prerequisites.
    prereq_errors = check_prerequisites()
    if prereq_errors:
        result.errors = prereq_errors
        return result

    # Step 2: Detect project structure.
    detection = detect_project(project_dir)

    # Apply language override if provided.
    if language:
        detection.language = language

    # Step 3: Interactive confirmation (if not --non-interactive).
    if not non_interactive and detection.language != "unknown":
        _print_detection(detection)
        if sys.stdin.isatty():
            answer = input("\nProceed with these settings? [Y/n] ").strip().lower()
            if answer and answer not in ("y", "yes"):
                print("Aborted.")
                return result
    elif detection.language == "unknown" and not language:
        if result.errors is None:
            result.errors = []
        result.errors.append(
            "Could not detect project language. "
            "Use --language to specify: dark-factory init --language python"
        )
        return result

    # Step 4: Create directory structure.
    _create_directories(project_dir, update=update)

    # Step 5: Copy framework files into project.
    _copy_framework_files(project_dir, update=update)

    # Step 6: Install skills globally.
    _install_skills_globally()

    # Step 7: Generate config.toml.
    config_path = project_dir / ".dark-factory" / "config.toml"
    if config_path.exists() and not non_interactive and sys.stdin.isatty():
        answer = input(f"\n{config_path} already exists. Overwrite? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Keeping existing config.")
            result.success = True
            result.config_path = str(config_path)
            _populate_result(result, detection)
            return result

    _write_config(config_path, detection)

    # Step 7b: Write bootstrap delta (operational config → delta log).
    _write_bootstrap_delta(project_dir, detection)

    # Step 7c: Write project-hints.toml (policy layer scaffold).
    # ``[policy]`` is ALWAYS preserved: it's hand-authored and must never
    # be clobbered by a re-run of ``init``. Re-detection refreshes
    # ``[detected]`` in place. The scaffold is emitted only when no file
    # exists yet. A malformed existing file is surfaced as an error, not
    # silently overwritten (would lose hand-authored rules).
    try:
        _write_project_hints(project_dir, detection)
    except ConfigError as e:
        if result.errors is None:
            result.errors = []
        result.errors.append(str(e))
        return result

    # Step 7d: If migrating, offer to delete the now-redundant legacy file.
    if migrate_hints:
        _migrate_legacy_hints(
            project_dir,
            non_interactive=non_interactive,
            yes=yes,
        )

    # Step 8: Populate result.
    result.success = True
    result.config_path = str(config_path)
    _populate_result(result, detection)

    # Step 9: emit `contracts init` hints for active adapters that
    # ship project-helper starters but haven't been init'd yet.
    # Closes the discoverability gap: the framework detects active
    # adapters at init time but doesn't otherwise tell the developer
    # they need to bring in the project-side helpers. Informational
    # only — does NOT auto-run `contracts init` because the
    # orchestrator's ``activate_spec_triggered_adapters`` (G1
    # backstop, ``orchestrator.py:418-449``) handles the case where
    # a spec authored later activates an adapter the init scan
    # missed; auto-running here would skip that case.
    _print_contracts_init_hints(project_dir)

    return result


def check_prerequisites() -> list[str]:
    """Check that all prerequisites are installed.

    Returns a list of actionable error messages (empty = all good).
    """
    from darkfactory.utils.subprocess_runner import run

    errors: list[str] = []

    # Docker.
    docker_which = run(["which", "docker"], timeout=5)
    if docker_which.exit_code != 0:
        errors.append(
            "Docker not installed.\n"
            "  macOS:  brew install --cask docker\n"
            "  Linux:  curl -fsSL https://get.docker.com | sh"
        )
    else:
        docker_info = run(["docker", "info"], timeout=10)
        if docker_info.exit_code != 0:
            errors.append(
                "Docker daemon not running.\n"
                "  macOS:  open -a Docker\n"
                "  Linux:  sudo systemctl start docker"
            )

    # Claude Code.
    claude_which = run(["which", "claude"], timeout=5)
    if claude_which.exit_code != 0:
        errors.append(
            "Claude Code not installed.\n"
            "  Install: npm install -g @anthropic-ai/claude-code"
        )

    # Git.
    git_which = run(["which", "git"], timeout=5)
    if git_which.exit_code != 0:
        errors.append(
            "Git not installed.\n"
            "  macOS:  xcode-select --install\n"
            "  Linux:  sudo apt install git  (or equivalent)"
        )
    else:
        # Check if inside a git repo.
        git_check = run(["git", "rev-parse", "--is-inside-work-tree"], timeout=5)
        if git_check.exit_code != 0:
            errors.append(
                "Not inside a git repository.\n"
                "  Run: git init"
            )

    # Python version (we need 3.11+ for tomllib).
    if sys.version_info < (3, 11):
        errors.append(
            f"Python {sys.version_info.major}.{sys.version_info.minor} detected, but 3.11+ required.\n"
            "  Upgrade: https://www.python.org/downloads/"
        )

    return errors


def _print_contracts_init_hints(project_dir: Path) -> None:
    """Suggest `dark-factory contracts init <adapter>` for each
    active adapter that ships project-helper starters but isn't
    init'd yet.

    Reuses the registry-driven detection signal that already
    populates ``[detected].active_adapters``; no new mechanism. The
    hint surfaces at the moment of relevance (init has just placed
    the project's `.dark-factory/` skeleton; the developer is
    looking at stdout). Skipped when an adapter's contracts dir
    already exists — re-running init on a populated project doesn't
    re-prompt.

    The orchestrator's spec-trigger backstop covers the orthogonal
    case: a spec authored later that activates an adapter the init
    scan missed.
    """
    from darkfactory.contracts.init import list_available_adapters

    detected = _detection_to_active_adapters(project_dir)
    eligible = set(list_available_adapters())
    needs_init = [
        a for a in detected
        if a in eligible
        and not (project_dir / ".dark-factory" / "contracts" / a).is_dir()
    ]
    if not needs_init:
        return
    print("\nNext steps — project-side adapter contracts:")
    for adapter in needs_init:
        print(f"  dark-factory contracts init {adapter}")


def _print_detection(detection: DetectionResult) -> None:
    """Print detected project characteristics."""
    print(f"\nDetected: {detection.language.capitalize()} project")
    if detection.source_dirs:
        print(f"  Source dirs: {', '.join(detection.source_dirs)}")
    if detection.test_dir:
        print(f"  Test dir:    {detection.test_dir}")
    if detection.framework:
        print(f"  Framework:   {detection.framework}")
    if detection.install_cmd:
        print(f"  Install:     {detection.install_cmd}")
    if detection.build_cmd:
        print(f"  Build:       {detection.build_cmd}")
    if detection.lint_cmd:
        print(f"  Lint:        {detection.lint_cmd}")
    if detection.test_cmd:
        print(f"  Test:        {detection.test_cmd}")


_DARK_FACTORY_GITIGNORE = """\
# Framework files — managed by dark-factory, refreshed via `dark-factory init --update`.
# Do not commit these; they are not project customizations.
agents/
templates/

# Runtime artifacts — generated during pipeline runs, not for version control.
scenarios/
audit/*
state/*
reports/*
metrics.jsonl
events.jsonl
events.jsonl.lock
worktrees/

# Ephemeral per-run artifacts in reviews/ — feedback, oracle output, escalation
# reports are only useful during the iteration loop. Validation files and
# review.md stay (review.md is copied to shipped-scenarios/<spec>/ on ship).
reviews/*-feedback-iter*.md
reviews/*-oracle-output-iter*.md
reviews/*-escalation.md

# Holdouts are adversarial scenarios written blind — not validated against
# shipped code. Skip unless building a regression suite.
holdouts/

# Advisory pre-check findings — stale after implementation, derivable.
precheck/

# Per-run snapshots written when a run is reset/aborted/superseded.
# Useful for post-mortem on a fresh failure, but accumulate fast and
# are derivable from logs/state. Don't version-control them.
archive/

# Preserve directory placeholders for ignored directories.
!audit/.gitkeep
!state/.gitkeep
!reports/.gitkeep

# shipped-scenarios/ is tracked — it contains the validated test suite,
# code review, defect registry, and metadata for each shipped spec.
# See docs/POST-SHIP-COMMIT.md for the complete post-ship commit guide.
"""


def _create_directories(project_dir: Path, *, update: bool = False) -> None:
    """Create the .dark-factory directory structure."""
    for dir_path in _REQUIRED_DIRS:
        full_path = project_dir / dir_path
        full_path.mkdir(parents=True, exist_ok=True)

    gitignore_path = project_dir / ".dark-factory" / ".gitignore"
    if not gitignore_path.exists() or update:
        gitignore_path.write_text(_DARK_FACTORY_GITIGNORE, encoding="utf-8")

    # shipped-scenarios/ is tracked; create a .gitkeep so the empty dir is committed.
    gitkeep = project_dir / ".dark-factory" / "shipped-scenarios" / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()

    print("\nDirectory structure created: .dark-factory/")


def _write_config(config_path: Path, detection: DetectionResult) -> None:
    """Generate and write config.toml from detection results.

    Phase 3: config.toml holds only pipeline tuning and the language
    field (needed for Docker image selection before the env contract
    loads). All operational config (commands, source_dirs, etc.) goes
    into the bootstrap delta via _write_bootstrap_delta().
    """
    lines: list[str] = []

    # Header comment.
    lines.append("# Dark Factory configuration")
    lines.append("# Generated by `dark-factory init`")
    lines.append("# Operational config (commands, dirs) lives in the environment")
    lines.append("# contract. See .dark-factory/architecture/environment.toml")
    lines.append("")

    # [core] — language only (needed for Docker image selection).
    lines.append("[core]")
    lines.append(f'language = "{detection.language}"')
    lines.append("")

    # [enterprise] — commented out by default.
    lines.append("# [enterprise]")
    lines.append("# enabled = true  # Uncomment to enable: deployment, Slack/webhook notifications, trust scoring, anomaly detection, OTEL")
    lines.append("")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n")
    print(f"Config written: {config_path}")


def _write_bootstrap_delta(project_dir: Path, detection: DetectionResult) -> None:
    """Write detection results as a bootstrap delta in the environment delta log.

    This replaces the old approach of writing operational fields into
    config.toml's [gates] section. The bootstrap delta is the seed for
    the environment contract — the Architect can override any field via
    subsequent deltas.
    """
    from darkfactory.architecture.amend import _next_delta_number, _render_delta
    from darkfactory.architecture.deltas import hash_view, materialize
    from darkfactory.architecture.environment import EnvironmentContract

    # Build ops from detection results.
    # Map: (DetectionResult field, environment contract path)
    _DETECT_TO_BOOTSTRAP = (
        ("language", "runtime.language"),
        ("package_manager", "runtime.package_manager"),
        ("build_cmd", "commands.build"),
        # Python's dep-install rides install_cmd -> commands.install so it runs at
        # the gate Install stage BEFORE Setup (a [dev]-declared migration tool is
        # then present for the Setup migration). Must mirror autodetect_delta's
        # _DETECT_TO_ENV — a fresh `dark-factory init` would otherwise drop it.
        ("install_cmd", "commands.install"),
        ("lint_cmd", "commands.lint"),
        ("typecheck_cmd", "commands.typecheck"),
        ("test_cmd", "commands.test"),
        ("setup_cmd", "commands.setup"),
        ("server_cmd", "commands.server_start"),
    )

    ops: list[tuple[str, object]] = []
    for detect_field, env_path in _DETECT_TO_BOOTSTRAP:
        value = getattr(detection, detect_field, "")
        if value and value != "unknown":
            ops.append((env_path, value))

    # source_dirs and test_dir use list/string → list paths.
    if detection.source_dirs:
        ops.append(("layout.source_dirs", detection.source_dirs))
    if detection.test_dir:
        ops.append(("layout.test_dirs", [detection.test_dir]))

    if not ops:
        return

    # Env path uniqueness: all keys in _DETECT_TO_BOOTSTRAP map to distinct
    # env paths, so sort is safe even with mixed value types.
    ops.sort()

    arch_dir = project_dir / ".dark-factory" / "architecture"
    deltas_dir = arch_dir / "environment.deltas"
    env_path = arch_dir / "environment.toml"
    deltas_dir.mkdir(parents=True, exist_ok=True)

    # Guard against re-init: if deltas already exist, skip bootstrap.
    # The user can amend the environment via the Architect or by editing
    # deltas directly. Re-running init should not corrupt the delta chain.
    from darkfactory.architecture.deltas import list_deltas
    if list_deltas(deltas_dir):
        log.info("bootstrap delta: skipping — deltas already exist in %s", deltas_dir)
        return

    parent_hash = hash_view(EnvironmentContract())
    n = _next_delta_number(deltas_dir)
    filename = f"{n:04d}-bootstrap-from-detection.toml"
    delta_path = deltas_dir / filename

    # Snapshot for rollback (same pattern as amend.py).
    env_snapshot: bytes | None
    if env_path.exists():
        env_snapshot = env_path.read_bytes()
    else:
        env_snapshot = None

    delta_path.write_text(
        _render_delta("bootstrap-from-detection", parent_hash, ops),
        encoding="utf-8",
    )
    log.info("bootstrap delta: wrote %s with %d op(s)", delta_path.name, len(ops))

    try:
        materialize(deltas_dir, env_path)
    except Exception:
        if env_snapshot is not None:
            env_path.write_bytes(env_snapshot)
        elif env_path.exists():
            env_path.unlink()
        delta_path.unlink(missing_ok=True)
        raise

    print(f"Environment contract written: {env_path}")


def _copy_framework_files(project_dir: Path, *, update: bool = False) -> None:
    """Copy agents/ and templates/ from the framework into .dark-factory/.

    This lets skills read agent definitions and spec templates from the local
    project instead of the installed package path, avoiding out-of-project
    permission prompts in Claude Code.
    """
    import darkfactory
    framework_dir = Path(darkfactory.__file__).parent.parent

    copied: list[str] = []
    skipped: list[str] = []

    for dirname in ("agents", "templates"):
        src = framework_dir / dirname
        dst = project_dir / ".dark-factory" / dirname
        if not src.is_dir():
            log.warning("framework directory not found: %s", src)
            continue
        if dst.exists():
            if update:
                shutil.rmtree(dst)
            else:
                skipped.append(dirname)
                continue
        shutil.copytree(src, dst)
        copied.append(dirname)

    if copied:
        print(f"Framework files copied: {', '.join(f'.dark-factory/{d}/' for d in copied)}")
    if skipped:
        print(f"Framework files already present (use --update to refresh): {', '.join(skipped)}")


def ensure_agent_definitions(project_dir: Path) -> list[str]:
    """Restore any MISSING agent-definition files from the framework package.

    The mounted-read roles (test-engineer, qa-holdout, spec-validator,
    substrate-owner, architect-for-its-non-injected-steps) are told by their task
    prompt to ``Read your agent definition at /project/.dark-factory/agents/<role>.md``
    at runtime. If that dir was stripped (it's gitignored as a "framework file
    managed by init", so a from-scratch reset can remove it) the agents get
    "File does not exist" and run BLIND of their role definitions — a SILENT,
    campaign-corrupting failure (bb spec-4: the whole campaign ran def-less).

    NON-DESTRUCTIVELY restores any missing ``<role>.md`` from the canonical
    framework package copy (the same source ``_copy_framework_files`` /
    ``_read_agent_definition`` use). Restores the WHOLE package ``agents/`` dir
    (not a hand-curated mounted-read subset that would rot vs barriers.py).
    Does NOT clobber an existing file (respects a local edit) and does NOT fix
    staleness (``init --update`` owns that). Pure file-op — returns the restored
    filenames; the caller logs.

    SOURCE-RESOLUTION (shared, pre-existing assumption — Codex review): the
    repo-root ``agents/`` dir lives OUTSIDE the importable package and is reached
    via ``Path(darkfactory.__file__).parent.parent`` — identical to
    ``_copy_framework_files`` and ``_read_agent_definition``. This is the
    framework's editable-install model (``agents/`` is NOT shipped in a wheel;
    see tests/unit/test_packaging_data_files.py). On a non-editable wheel install
    the source dir is absent → this returns [] (a no-op, exactly as init's copy
    and the injected-role reads already do there). It heals where the framework
    actually runs (editable / repo-present); making ``agents/`` wheel-shippable is
    a separate, broader packaging change. Best-effort: missing framework
    ``agents/`` returns [] rather than raising.
    """
    import darkfactory
    framework_agents = Path(darkfactory.__file__).parent.parent / "agents"
    if not framework_agents.is_dir():
        return []
    dst_dir = project_dir / ".dark-factory" / "agents"
    restored: list[str] = []
    for src in sorted(framework_agents.glob("*.md")):
        dst = dst_dir / src.name
        if dst.is_file():
            continue  # present — never clobber (staleness is init --update's job)
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(src.name)
        except OSError as exc:
            log.warning("could not restore agent definition %s: %s", src.name, exc)
    return restored


def detect_agent_definition_drift(project_dir: Path) -> list[str]:
    """Return PRESENT project agent defs whose bytes differ from the framework
    package copy (the non-destructive sibling of ``ensure_agent_definitions``).

    ``ensure_agent_definitions`` only restores MISSING defs — it never clobbers a
    present file, so a present-but-STALE ``<role>.md`` (e.g. a from-scratch reset
    that restored an OLD commit) silently stays the mounted-read truth while the
    framework has newer role rules. That's a narrower reintroduction of the
    missing-def bug (the TE runs OUTDATED scenario rules). This DETECTS it so the
    caller can WARN — it does NOT overwrite (clobber-on-run would stomp a real
    local edit; freshness is ``init --update``'s job). Pure read-only file-op.

    Shares the editable-install source assumption of ``ensure_agent_definitions``
    (a wheel has no framework ``agents/`` → returns []).
    """
    import darkfactory
    framework_agents = Path(darkfactory.__file__).parent.parent / "agents"
    if not framework_agents.is_dir():
        return []
    dst_dir = project_dir / ".dark-factory" / "agents"
    drifted: list[str] = []
    for src in sorted(framework_agents.glob("*.md")):
        dst = dst_dir / src.name
        if not dst.is_file():
            continue  # missing → ensure_agent_definitions restores it (not drift)
        try:
            if src.read_bytes() != dst.read_bytes():
                drifted.append(src.name)
        except OSError as exc:
            log.warning("could not compare agent definition %s: %s", src.name, exc)
    return drifted


def _install_skills_globally() -> None:
    """Symlink dark-factory skills into ~/.claude/skills/ so they're available in every project."""
    import darkfactory
    framework_skills = Path(darkfactory.__file__).parent.parent / ".claude" / "skills"
    if not framework_skills.is_dir():
        log.warning("skills directory not found: %s", framework_skills)
        return

    global_skills = Path.home() / ".claude" / "skills"
    global_skills.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for skill_dir in sorted(framework_skills.iterdir()):
        if not skill_dir.is_dir() or not skill_dir.name.startswith("df-"):
            continue
        link = global_skills / skill_dir.name
        if link.is_symlink() and link.resolve() == skill_dir.resolve():
            continue  # already up to date
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(skill_dir)
        installed.append(skill_dir.name)

    if installed:
        print(f"Skills installed globally: {', '.join(installed)}")
    else:
        print("Skills already up to date in ~/.claude/skills/")


def _populate_result(result: InitResult, detection: DetectionResult) -> None:
    """Copy detection results into InitResult."""
    result.language = detection.language
    result.source_dirs = detection.source_dirs
    result.test_dir = detection.test_dir
    result.framework = detection.framework


# ----- project-hints.toml writer (B2) ---------------------------------------


# Commented-out [policy] scaffold written on first init. Every line starts
# with "# " so a round-trip through tomllib produces an empty policy — the
# scaffold is documentation, not active configuration. Editors who
# uncomment lines opt into the policy explicitly; accidental "save without
# editing" leaves the file safely empty.
_POLICY_SCAFFOLD = """\
# ----- Policy (human-authored, additive) ------------------------------------
# Project-level standing rules consumed by the architect, consistency checks,
# and workspace guards. Uncomment and edit to opt in. Leaving these commented
# is fine — an empty [policy] is valid.

# [policy.workspace]
# scope = "apps/api"                          # primary workspace path (monorepos)
# banned_recursive = ["pnpm -r", "pnpm --recursive"]

# [policy.migrations]
# mode = "cli-only"                           # "cli-only" | "on-start" | ""
# command = "pnpm db:migrate"                 # architect should set this as commands.setup

# [[policy.consistency_rules]]
# name = "example-rule"                       # unique; used as dedup key in remediation
# spec_patterns = ["phrase in the spec"]      # regex patterns (compiled at check-time)
# forbidden_env = ["pattern in env"]
# env_paths = ["commands.*"]
# description = "Why this rule exists — cite the post-mortem if any."
"""


def _detection_to_active_adapters(project_dir: Path) -> list[str]:
    """Map project state to the adapter registry names A5 consumes.

    Registry-driven: iterate the registry and ask each adapter whether
    it activates for the given project. This is the single seam where
    project-state → adapter-names happens; adding a new adapter is one
    line in ``REGISTRY`` and zero changes here.

    False-positive activation is worse than a conservative empty list
    (an unregistered name fails A5 fail-fast), so each adapter's
    ``detect()`` is the source of truth.

    Order preservation: iterating ``REGISTRY.items()`` yields adapters
    in declaration order (Python dicts are insertion-ordered). That
    order is written verbatim into ``[detected].active_adapters`` and
    re-loaded in the same order by ``load_active_adapters`` so
    architect addenda concatenate predictably. A prior draft used
    ``sorted()`` here — do not reinstate without also updating the A5
    ordering contract and every docstring that promises declaration
    order.
    """
    from darkfactory.adapters import REGISTRY

    return [
        name
        for name, cls in REGISTRY.items()
        if cls(project_dir=project_dir).detect()
    ]


def _build_detected_table(
    detection: DetectionResult, project_dir: Path
) -> dict[str, object]:
    """Build the [detected] TOML table from detection.

    Matches the field set enforced by ``project_hints._DETECTED_KEYS`` so
    a round-trip through ``load_project_hints`` is clean. ``detection``
    supplies the language-oriented fields; ``project_dir`` is required
    so the registry-driven adapter probe can run each adapter's
    ``detect()`` against the real tree.
    """
    from darkfactory.architecture.project_hints import CURRENT_SCHEMA_VERSION

    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "language": detection.language if detection.language != "unknown" else "",
        "package_manager": detection.package_manager,
        "workspace_layout": detection.workspace_layout,
        "typescript": detection.typescript,
        "test_runner": detection.test_runner,
        "active_adapters": _detection_to_active_adapters(project_dir),
        "lockfiles": list(detection.lockfiles),
    }


def _reject_symlink(path: Path, *, reason: str) -> None:
    """Refuse to write / delete through a symlink.

    ``project-hints.toml`` is a trust-sensitive artifact: its contents
    feed the architect's prompt and consistency checks. A symlink at
    that path would let an attacker (or a sloppy setup script that ran
    ``ln -s``) redirect the write or delete to somewhere the user cares
    about. We refuse rather than ``realpath``-resolve because the user
    almost certainly didn't intend the redirect.
    """
    if path.is_symlink():
        raise ConfigError(
            f"{path}: refusing to {reason} through symlink (target: "
            f"{path.resolve()})"
        )


def _write_project_hints(
    project_dir: Path,
    detection: DetectionResult,
) -> Path:
    """Write ``.dark-factory/project-hints.toml``.

    Semantics are intentionally **policy-preserving, always**:

    * ``[detected]`` is machine-written and re-derivable — it's refreshed
      on every call.
    * ``[policy]`` is human-authored. If a parseable policy section
      exists in the current file, it is preserved verbatim (via a
      tomllib → tomli_w round-trip). If the file doesn't exist or
      contains no [policy] content, a commented-out scaffold is emitted
      as documentation.
    * A present-but-unparseable file raises ``ConfigError``. Silent
      overwrite would lose hand-authored rules; better to fail loudly
      and let the user either fix the TOML or explicitly remove the file.

    Symlink guard: refuses to write if the target path is a symlink
    (blast-radius containment — someone could otherwise redirect our
    writes to a sensitive file).
    """
    import tomli_w

    hints_path = project_dir / ".dark-factory" / "project-hints.toml"
    hints_path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(hints_path, reason="write")

    detected = _build_detected_table(detection, project_dir)

    # Preserve mode is *always on*. If the file doesn't exist, we get
    # back "" and emit the scaffold. If it exists and parses, we
    # preserve its policy verbatim. If it exists and doesn't parse, we
    # raise (handled above).
    preserved_policy_text = ""
    if hints_path.is_file():
        preserved_policy_text = _extract_existing_policy(hints_path)

    lines: list[str] = []
    lines.append("# Dark Factory project hints")
    lines.append("# [detected] is auto-written by `dark-factory init`; refreshed by")
    lines.append("# `dark-factory init --update` and `dark-factory init --migrate-hints`.")
    lines.append("# Hand-edits to [detected] will be overwritten — put standing rules in")
    lines.append("# [policy] instead.")
    lines.append("")
    lines.append("[detected]")
    # tomli_w.dumps on the isolated table keeps key order and escapes strings.
    lines.append(tomli_w.dumps(detected).rstrip())
    lines.append("")

    if preserved_policy_text:
        # Preserved block already includes its own [policy.*] header lines.
        lines.append(preserved_policy_text.rstrip())
        lines.append("")
    else:
        lines.append(_POLICY_SCAFFOLD.rstrip())
        lines.append("")

    hints_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Project hints written: {hints_path}")
    return hints_path


def _extract_existing_policy(hints_path: Path) -> str:
    """Return the TOML text for an existing [policy] section, re-rendered.

    Round-trips through ``tomllib`` + ``tomli_w`` rather than string-slicing
    the source file: the original may have trailing inline comments, weird
    whitespace, or the scaffold (commented lines that parse to empty
    policy). Re-rendering from the parsed value is the only way to
    guarantee we emit a section semantically identical to what the loader
    sees. Empty policy → empty string, so the scaffold gets re-applied by
    the caller (no drift toward a silently-empty policy section).

    Raises ``ConfigError`` on unparseable TOML — silent overwrite would
    lose hand-authored policy rules, which is a worse failure mode than
    telling the user to fix the file first.
    """
    import tomli_w
    import tomllib
    try:
        raw = tomllib.loads(hints_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(
            f"{hints_path}: unreadable ({e}); refusing to overwrite — "
            f"hand-authored [policy] would be lost. Fix file permissions "
            f"or delete the file and re-run."
        ) from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(
            f"{hints_path}: invalid TOML ({e}); refusing to overwrite — "
            f"hand-authored [policy] would be lost. Fix the syntax error "
            f"or delete the file and re-run."
        ) from e
    policy = raw.get("policy") if isinstance(raw, dict) else None
    if not isinstance(policy, dict) or not policy:
        return ""
    # tomli_w with a top-level wrapper preserves the [policy.*] nested
    # tables (rather than emitting bare keys under no header).
    return tomli_w.dumps({"policy": policy})


# ----- Legacy hints.json migration (B2) -------------------------------------


def _migrate_legacy_hints(
    project_dir: Path,
    *,
    non_interactive: bool,
    yes: bool,
) -> None:
    """Offer to delete the now-redundant legacy ``hints.json``.

    ``project-hints.toml`` fully subsumes ``hints.json`` for the
    ``[detected]`` fields; keeping both invites stale-cache bugs where
    ``hints.json`` says one thing and ``project-hints.toml`` says another.
    But deletion is the user's call — the default is to keep the file and
    trust the user to remove it manually once they've validated the
    migration.

    ``environment.toml`` is NOT touched here: it remains the architect's
    runtime contract (amended via deltas) and is architecturally distinct
    from the hints file. Future work may consolidate them further; this
    migration is intentionally scoped.

    Args:
        non_interactive: disables the prompt entirely (CI / scripted runs
            get the safe default: keep the legacy file).
        yes: forces deletion without prompting (explicit opt-in for CI).
    """
    legacy = project_dir / ".dark-factory" / "architecture" / "hints.json"
    if not legacy.is_file():
        log.debug("legacy migration: no hints.json found at %s", legacy)
        return

    # Symlink guard: someone pointing hints.json at a file they care about
    # should not have it silently unlinked by a migration tool.
    _reject_symlink(legacy, reason="delete")

    def _try_unlink() -> None:
        """Robust delete — surface failure as a log line, not a crash.

        project-hints.toml was already written successfully; losing the
        deletion step shouldn't abort init. The user can remove
        hints.json manually afterwards.
        """
        try:
            legacy.unlink()
            print(f"Deleted legacy file: {legacy}")
        except OSError as e:
            log.warning(
                "legacy migration: project-hints.toml was written but "
                "could not remove %s: %s — delete manually once verified",
                legacy, e,
            )
            print(
                f"\nWARNING: {legacy} could not be deleted: {e}\n"
                "  project-hints.toml was written successfully; remove "
                "the legacy file manually once you've verified the "
                "migration."
            )

    if yes:
        _try_unlink()
        return

    if non_interactive or not sys.stdin.isatty():
        print(
            f"\nLegacy file still present: {legacy}\n"
            "  project-hints.toml now carries this data. Run with "
            "--migrate-hints --yes to delete, or remove manually after "
            "verifying the migration."
        )
        return

    prompt = (
        f"\nDelete legacy {legacy}?\n"
        "  (project-hints.toml now carries this data; keep = N) [y/N] "
    )
    answer = input(prompt).strip().lower()
    if answer in ("y", "yes"):
        _try_unlink()
    else:
        print(f"Kept: {legacy}")
