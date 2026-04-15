"""Project-hints writer — Sprint 4 / B3.

Writer helpers for ``.dark-factory/project-hints.toml``. The loader
(`project_hints.py`) is strictly read-side; this module is strictly
write-side. Split because:

* Comment preservation is only a writer concern. The loader drops comments
  by design (tomllib). Append-only writes let us extend the file without
  reformatting human-authored content.
* Writers have to import ``tomli_w``; readers use only the stdlib.
* Keeping the loader regex-/string-manipulation-free makes it
  easy to reason about — any round-trip surprises live here.

Two operations are exposed:

1. :func:`append_consistency_rule` — append one
   ``[[policy.consistency_rules]]`` block to an existing file. Safe for
   comment preservation. With ``force=True`` on a duplicate name, does
   a **surgical** text replacement of just that rule's stanza —
   comments elsewhere in the file are preserved.

2. :func:`init_policy` — write a greenfield ``[policy]`` section by
   full-file round-trip. Preserves ``[detected]`` if present but DROPS
   comments (the alternative — surgical edit — is not worth the
   complexity for a one-shot initialization).

Both functions are atomic (tempfile + ``os.replace``). Both validate by
re-parsing after the write and **restore the previous file** on
validation failure — designed to be safe for automation callers (C2
remediation writes this file unattended, so "leave broken file on disk"
is not an acceptable post-error state).

Both functions refuse to write when the project is still on the legacy
``environment.toml`` + ``hints.json`` pair without ``project-hints.toml``.
Writing a fresh file in that state silently shadows the legacy
``[detected]`` facts (the loader prefers ``project-hints.toml``) and
strands the legacy data. The loader documents this as the
``synthesized_from_legacy`` contract — consumers that persist MUST
check the flag before writing.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import tempfile
import tomllib
from pathlib import Path

import tomli_w

from darkfactory.architecture.project_hints import (
    PROJECT_HINTS_FILENAME,
    ConsistencyRuleSpec,
    PolicySection,
    WorkspacePolicy,
    MigrationsPolicy,
    load_project_hints,
)
from darkfactory.errors import ConfigError

log = logging.getLogger(__name__)


# ----- Public API ------------------------------------------------------------


def project_hints_path(project_dir: Path) -> Path:
    """Absolute path to ``.dark-factory/project-hints.toml`` for a project."""
    return project_dir / ".dark-factory" / PROJECT_HINTS_FILENAME


def append_consistency_rule(
    project_dir: Path,
    rule: ConsistencyRuleSpec,
    *,
    force: bool = False,
) -> None:
    """Append a consistency rule to ``[policy.consistency_rules]``.

    Without ``force``: raises :class:`ConfigError` if a rule with the
    same ``name`` already exists. Append-only — existing file bytes
    (including comments) are preserved verbatim.

    With ``force``: if a rule with the same name exists, the stanza is
    surgically replaced in-place. Comments inside the replaced stanza
    are lost; comments elsewhere in the file are preserved.

    File is created (with a minimal header comment) if it does not yet
    exist.

    Raises:
        ConfigError: on duplicate name without ``force``, on legacy
            ``hints.json`` still present without migration, or if the
            resulting file does not round-trip through
            :func:`load_project_hints`.
        OSError: on underlying filesystem failure.
    """
    path = project_hints_path(project_dir)
    _refuse_if_legacy_unmigrated(project_dir, path)

    if not path.exists():
        _create_with_first_rule(project_dir, path, rule)
        return

    existing_text = path.read_text(encoding="utf-8")
    try:
        existing = tomllib.loads(existing_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{path}: cannot append to malformed TOML: {exc}. "
            f"Run `dark-factory hints lint` to diagnose."
        ) from exc

    rules_raw = (
        existing.get("policy", {}).get("consistency_rules", [])
        if isinstance(existing.get("policy"), dict) else []
    )
    existing_names = {
        r["name"] for r in rules_raw
        if isinstance(r, dict) and isinstance(r.get("name"), str)
    }

    if rule.name in existing_names:
        if not force:
            raise ConfigError(
                f"rule {rule.name!r} already exists in {path}. "
                f"Pass --force to overwrite (only the target rule's stanza "
                f"is rewritten; comments elsewhere are preserved)."
            )
        new_text = _replace_rule_stanza(existing_text, rule)
    else:
        new_text = _append_rule_text(existing_text, rule)

    _atomic_write_and_validate(project_dir, path, new_text)


def init_policy(
    project_dir: Path,
    policy: PolicySection,
    *,
    force: bool = False,
) -> None:
    """Write a ``[policy]`` section for first-time policy setup.

    If ``project-hints.toml`` already exists and has a non-empty
    ``[policy]`` section, refuses (raises :class:`ConfigError`) unless
    ``force=True``. This protects user-authored policy from being
    silently clobbered by a repeat of the interactive walkthrough.

    ``[detected]`` is preserved if present. **Round-trip drops comments**
    in any existing section — hints init is expected to run on a file
    that is either empty or has only auto-generated ``[detected]``.

    Raises:
        ConfigError: if the file has an existing non-empty ``[policy]``
            and ``force`` is False, if legacy ``hints.json`` is still
            present without migration, or if the written file does not
            round-trip through :func:`load_project_hints`.
    """
    path = project_hints_path(project_dir)
    _refuse_if_legacy_unmigrated(project_dir, path)

    existing: dict = {}
    if path.exists():
        try:
            existing = tomllib.loads(
                path.read_text(encoding="utf-8")
            )
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"{path}: cannot init over malformed TOML: {exc}"
            ) from exc
        existing_policy = existing.get("policy")
        if isinstance(existing_policy, dict) and existing_policy and not force:
            raise ConfigError(
                f"{path} already has a [policy] section. "
                f"Pass --force to overwrite — note that this rewrites the "
                f"file and does not preserve comments. To extend an "
                f"existing policy, use `dark-factory hints add`."
            )

    new_doc: dict = {}
    if isinstance(existing.get("detected"), dict):
        new_doc["detected"] = existing["detected"]
    new_doc["policy"] = _policy_to_dict(policy)

    _atomic_write_and_validate(project_dir, path, tomli_w.dumps(new_doc))


# ----- Internals -------------------------------------------------------------


def _refuse_if_legacy_unmigrated(project_dir: Path, path: Path) -> None:
    """Refuse to write when legacy ``hints.json`` is still live.

    The loader's :attr:`ProjectHints.synthesized_from_legacy` contract
    says consumers that persist MUST check this flag. This is where
    we check. A fresh write on a pre-migration project would create a
    bare ``project-hints.toml`` that silently shadows the legacy
    ``[detected]`` facts — the loader prefers the TOML and the JSON is
    never read again.
    """
    if path.exists():
        # The target file already exists; loader will not fall back to
        # legacy synthesis. Safe to proceed.
        return
    legacy_hints = (
        project_dir / ".dark-factory" / "architecture" / "hints.json"
    )
    legacy_env = (
        project_dir / ".dark-factory" / "architecture" / "environment.toml"
    )
    if legacy_hints.exists() or legacy_env.exists():
        raise ConfigError(
            f"legacy hints file(s) present but {path.name} is missing. "
            f"Run `dark-factory init --migrate-hints` to consolidate "
            f"before using `dark-factory hints`; writing now would strand "
            f"the legacy [detected] facts.",
        )


def _append_rule_text(existing_text: str, rule: ConsistencyRuleSpec) -> str:
    """Return ``existing_text`` with one rule stanza appended.

    Trailing whitespace in the original is normalized to exactly one
    blank line before the new header — consistent across line-ending
    conventions (CRLF and LF) and leaves the file with a final newline.
    """
    trimmed = existing_text.rstrip()
    block = _format_rule_block(rule)
    if trimmed:
        return f"{trimmed}\n\n{block}"
    return block


def _replace_rule_stanza(
    existing_text: str, rule: ConsistencyRuleSpec,
) -> str:
    """Surgically replace one ``[[policy.consistency_rules]]`` stanza.

    Matches the stanza whose `name = "<rule.name>"` line appears within
    the stanza span (header to next top-level/AoT header or EOF).
    Comments outside the stanza are preserved; comments inside the
    replaced stanza are lost (the new block comes from ``tomli_w`` and
    carries no comments).

    The regex is intentionally strict (anchored at line start, requires
    the exact AoT header) — if it doesn't match we raise rather than
    guess. The caller has already verified the rule exists by parsed
    name, so a non-match here means the file's on-disk layout is
    unusual (e.g. inline AoT syntax that the scanner doesn't recognize);
    failing loud beats a wrong edit.
    """
    # Find each [[policy.consistency_rules]] header. Stanza extends to
    # the next header of any top-level-like form ([foo], [[foo.bar]],
    # etc.) or EOF.
    header_re = re.compile(
        r"^\[\[policy\.consistency_rules\]\][ \t]*$", re.MULTILINE,
    )
    next_header_re = re.compile(
        r"^(?:\[[^\]]+\]|\[\[[^\]]+\]\])[ \t]*$", re.MULTILINE,
    )

    headers = list(header_re.finditer(existing_text))
    if not headers:
        raise ConfigError(
            "--force replace: no [[policy.consistency_rules]] stanza "
            "found in file (unexpected — loader said the rule exists)."
        )

    # name-line regex scoped to a stanza: tolerates both quoted forms.
    name_line_re = re.compile(
        rf'^[ \t]*name[ \t]*=[ \t]*(?:"{re.escape(rule.name)}"|'
        rf"'{re.escape(rule.name)}')[ \t]*$",
        re.MULTILINE,
    )

    for i, hdr in enumerate(headers):
        stanza_start = hdr.start()
        # Find the end: the next matching header of any kind, skipping
        # past the opening one we're on.
        search_from = hdr.end()
        m = next_header_re.search(existing_text, search_from)
        stanza_end = m.start() if m else len(existing_text)

        stanza_text = existing_text[stanza_start:stanza_end]
        if name_line_re.search(stanza_text):
            new_block = _format_rule_block(rule)
            # Preserve exactly one blank line after the replacement,
            # matching the separation convention used by append.
            before = existing_text[:stanza_start].rstrip()
            after = existing_text[stanza_end:].lstrip("\n")
            if before and after:
                return f"{before}\n\n{new_block.rstrip()}\n\n{after}"
            if before:
                return f"{before}\n\n{new_block}"
            if after:
                return f"{new_block.rstrip()}\n\n{after}"
            return new_block

    raise ConfigError(
        f"--force replace: could not locate the stanza with "
        f"name={rule.name!r} in file (unexpected — loader said the rule "
        f"exists)."
    )


def _create_with_first_rule(
    project_dir: Path, path: Path, rule: ConsistencyRuleSpec,
) -> None:
    """Create the file with a header comment and the first rule."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# project-hints.toml — project-level policy.\n"
        "# Written by `dark-factory hints`. Safe to hand-edit.\n"
        "# See docs/ADAPTER-GOVERNANCE.md for policy-vs-adapter rules.\n"
        "\n"
    )
    block = _format_rule_block(rule)
    _atomic_write_and_validate(project_dir, path, header + block)


def _format_rule_block(rule: ConsistencyRuleSpec) -> str:
    """Render one rule as its ``[[policy.consistency_rules]]`` stanza.

    Uses ``tomli_w`` for the quoting; slicing the output is safe because
    a single-entry AoT renders as exactly one header + its keys and no
    standalone ``[policy]`` header.
    """
    return tomli_w.dumps(
        {"policy": {"consistency_rules": [_rule_to_dict(rule)]}}
    )


def _rule_to_dict(rule: ConsistencyRuleSpec) -> dict:
    """Dataclass → TOML-serializable dict.

    Tuples become lists (TOML arrays) via ``asdict``.
    """
    d = dataclasses.asdict(rule)
    # ``asdict`` preserves tuples as tuples; tomli_w handles both but
    # downstream round-trip equality is cleaner with lists.
    for k in ("spec_patterns", "forbidden_env", "env_paths"):
        d[k] = list(d.get(k, ()))
    return d


def _policy_to_dict(policy: PolicySection) -> dict:
    """Render a ``PolicySection`` as the TOML-shaped ``[policy]`` dict.

    Drops empty sub-sections so the written file shows only what the
    user actually set — empty ``[policy.workspace]`` with all defaults
    would be noise. Uses dataclass equality so adding a new field to
    ``WorkspacePolicy`` / ``MigrationsPolicy`` automatically participates.
    """
    out: dict = {}
    if policy.workspace != WorkspacePolicy():
        out["workspace"] = {
            "scope": policy.workspace.scope,
            "banned_recursive": list(policy.workspace.banned_recursive),
        }
    if policy.migrations != MigrationsPolicy():
        out["migrations"] = {
            "mode": policy.migrations.mode,
            "command": policy.migrations.command,
        }
    if policy.consistency_rules:
        out["consistency_rules"] = [
            _rule_to_dict(r) for r in policy.consistency_rules
        ]
    return out


def _atomic_write_and_validate(
    project_dir: Path, path: Path, content: str,
) -> None:
    """Write ``content`` atomically and verify it round-trips.

    On validation failure, the previous file bytes are restored so the
    on-disk state is consistent for automation callers (C2 remediation
    writes unattended). A leaked broken file would crash the next agent
    that reads ``project-hints.toml``.

    ``project_dir`` is passed explicitly rather than derived from
    ``path`` — don't do string math on `.dark-factory` hierarchy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pre_write_bytes = path.read_bytes() if path.exists() else None

    dir_ = str(path.parent)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp", prefix=".project-hints-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Validate round-trip. If the loader raises, restore the previous
    # bytes (or remove the file if there was no previous version) before
    # re-raising — automation callers require a consistent on-disk
    # state after an error.
    try:
        load_project_hints(project_dir)
    except ConfigError:
        log.error(
            "%s: wrote %d bytes but resulting file did not validate; "
            "restoring previous content",
            path, len(content),
        )
        try:
            if pre_write_bytes is None:
                path.unlink()
            else:
                path.write_bytes(pre_write_bytes)
        except OSError as restore_exc:
            # Restore failed — log but raise the original error; the
            # user/caller needs to know their write was rejected, and
            # chaining the restore error would bury that.
            log.error(
                "%s: could not restore previous bytes: %s",
                path, restore_exc,
            )
        raise
