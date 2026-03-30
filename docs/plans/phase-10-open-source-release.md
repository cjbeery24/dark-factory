# Phase 10: Open-Source Release Preparation

## Context

Phases 1-9 deliver a production-grade, plug-and-play AI development framework with information barriers, concurrent execution, progressive deployment, digital twins, onboarding automation, and cold-start mitigation. The framework is **functionally complete**.

However, the repository is structured as an internal development project. It lacks standard open-source scaffolding (README, LICENSE file, CONTRIBUTING guide), contains private artifacts (business plan, internal phase plans, review history, personal config), and has an incomplete `.gitignore`. These gaps block credible public release on GitHub and PyPI.

**Goal:** A developer discovering Dark Factory on GitHub or PyPI can understand what it does, install it, and run their first pipeline — all within 15 minutes — without encountering private artifacts, broken paths, or missing context.

---

## Sub-Phase 10A: Sanitize Repository

**Items:** Remove private/personal content (1), expand `.gitignore` (2), clean tracked artifacts (3)

**Rationale:** This must come first. Publishing the repo with personal paths, a business plan, or internal roadmap documents would be embarrassing at best and strategically harmful at worst. Every subsequent sub-phase assumes a clean repository.

### Deliverables

1. **Remove `.claude/settings.local.json` from git** — Currently tracked with hardcoded paths (`~/sites/dark-factory/.venv/bin/python`, etc.). Must be untracked and gitignored. The `df-*` skill files under `.claude/skills/` should remain tracked — they are a core part of the framework's Claude Code integration.

2. **Expand `.gitignore`** — Current `.gitignore` has only 3 entries. Must cover:
   ```
   # User-local config
   .claude/settings.local.json
   .claude/settings.*.json

   # Python
   .venv/
   *.pyc
   dist/
   build/

   # OS
   .DS_Store

   # Secrets
   .env
   .env.*

   # Generated indexes
   .gitnexus/
   ```

3. **Remove or relocate private documents** — The following files must not appear in the public repository:
   | File | Action | Reason |
   |------|--------|--------|
   | `docs/business-plan.md` | Delete from repo (move to private location) | Contains pricing strategy, revenue projections, competitive positioning, financial projections |
   | `docs/plans/phase-4-concurrent-execution.md` | Delete or move | Internal development roadmap |
   | `docs/plans/phase-5-observability-intelligence.md` | Delete or move | Internal development roadmap |
   | `docs/plans/phase-5-gap-closure.md` | Delete or move | Internal development roadmap |
   | `docs/plans/phase-6-progressive-deployment.md` | Delete or move | Internal development roadmap |
   | `docs/plans/phase-7-digital-twins-scale.md` | Delete or move | Internal development roadmap |
   | `docs/plans/phase-8-plug-and-play.md` | Delete or move | Internal development roadmap |
   | `docs/plans/phase-9-cold-start-mitigation.md` | Delete or move | Internal development roadmap, SaaS-specific strategy |
   | `docs/plans/phase-10-open-source-release.md` | Delete or move (this file) | Internal meta-plan |
   | `docs/reviews/2026-02-28-v1-critical-evaluation.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-02-28-v2-docker-isolation-plan.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-02-28-v2.1-docker-isolation-plan.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-02-28-v2-critical-evaluation.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-03-02-v3-critical-evaluation.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-03-03-v4-critical-evaluation.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-03-03-v5-critical-evaluation.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-03-04-v6-critical-evaluation.md` | Delete or move | Internal development review history |
   | `docs/reviews/2026-03-04-production-readiness-analysis.md` | Delete or move | Internal development review history |

   **Recommendation:** Archive these to a separate private repo or local backup before deleting. They document valuable development history but are not user-facing.

### Decision required

> **Q-1: Keep `docs/plans/` and `docs/reviews/` as "development history" or remove entirely?**
>
> Options:
> - **(A) Remove entirely** — Cleaner repo, no clutter for new users. Recommended.
> - **(B) Keep as archived history** — Shows development rigor, but adds noise.
> - **(C) Move to a wiki** — Best of both worlds, but requires GitHub Wiki setup.
>
> This plan assumes **(A)**. Adjust Sub-Phase 10A deliverable 3 if a different option is chosen.

**Complexity:** Small (1-2 hours)

---

## Sub-Phase 10B: Standard Open-Source Files

**Items:** README (4), LICENSE file (5), CONTRIBUTING guide (6), CHANGELOG (7)

**Rationale:** These are table-stakes for any open-source project. GitHub renders README.md as the landing page, PyPI pulls the readme for the package description, and contributors need clear guidelines. Without these, the project looks abandoned or internal-only.

### Deliverables

1. **`README.md`** — The front door to the project. Must include:
   - **Elevator pitch** (2-3 sentences): what Dark Factory does, key innovation (information barriers between agents)
   - **Key features** (bulleted): blind testing, spec-driven, multi-language, zero runtime deps, Docker isolation
   - **Quick install**: `pip install dark-factory`
   - **30-second demo**: `dark-factory init` → `dark-factory new-spec` → `dark-factory run`
   - **Prerequisites**: Python 3.11+, Docker, Claude Code
   - **Documentation links**: QUICKSTART, WORKFLOW, INTEGRATION, SPECIFICATION-GUIDE
   - **License badge**, **Python version badge**
   - Link to `CONTRIBUTING.md`

2. **`LICENSE`** — MIT license full text file at project root. `pyproject.toml` already declares `license = "MIT"` but the actual file is missing.

3. **`CONTRIBUTING.md`** — Must cover:
   - How to report bugs (GitHub Issues, with template)
   - How to propose features (GitHub Discussions or Issues)
   - Development setup: `pip install -e ".[dev]"`, running tests (`python -m pytest`)
   - Code style and conventions (existing patterns, no additional linters needed)
   - PR process: fork, branch, test, submit
   - Code of conduct (link to Contributor Covenant or similar)

4. **`CHANGELOG.md`** — Initial entry for v1.0.0 summarizing what ships. Use [Keep a Changelog](https://keepachangelog.com/) format. Sections: Added, Changed, Fixed, Security.

### Files to create

| File | Purpose |
|------|---------|
| `README.md` | Project landing page for GitHub and PyPI |
| `LICENSE` | MIT license full text |
| `CONTRIBUTING.md` | Contributor guidelines |
| `CHANGELOG.md` | Release history |

### Files to modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `readme = "README.md"`, `authors`, `homepage`, `repository`, `documentation`, `keywords`, `classifiers` fields |

**Complexity:** Medium (2-3 hours)

---

## Sub-Phase 10C: PyPI Packaging Polish

**Items:** Package metadata (8), dev dependencies (9), package data inclusion (10)

**Rationale:** `pyproject.toml` is functional but sparse. PyPI listings need rich metadata for discoverability. Contributors need declared dev dependencies. Non-Python files (templates, agents, docs) must be explicitly included in the distribution.

### Deliverables

1. **Enrich `pyproject.toml` metadata** — Add all standard fields:
   ```toml
   [project]
   name = "dark-factory"
   version = "1.0.0"
   description = "Specification-driven AI development framework with information barriers"
   readme = "README.md"
   requires-python = ">=3.11"
   license = "MIT"
   authors = [{ name = "...", email = "..." }]
   keywords = ["ai", "claude", "testing", "specification", "development-framework"]
   classifiers = [
       "Development Status :: 4 - Beta",
       "Intended Audience :: Developers",
       "License :: OSI Approved :: MIT License",
       "Programming Language :: Python :: 3.11",
       "Programming Language :: Python :: 3.12",
       "Programming Language :: Python :: 3.13",
       "Topic :: Software Development :: Testing",
       "Topic :: Software Development :: Quality Assurance",
   ]

   [project.urls]
   Homepage = "https://github.com/<org>/dark-factory"
   Documentation = "https://github.com/<org>/dark-factory/blob/main/docs/QUICKSTART.md"
   Repository = "https://github.com/<org>/dark-factory"
   "Bug Tracker" = "https://github.com/<org>/dark-factory/issues"
   Changelog = "https://github.com/<org>/dark-factory/blob/main/CHANGELOG.md"
   ```

2. **Declare dev dependencies** — Contributors need to know what to install:
   ```toml
   [project.optional-dependencies]
   dev = ["pytest>=7.0"]
   ```

3. **Ensure non-Python files are included in sdist/wheel** — Templates, agent definitions, Docker files, and skills must ship with the package. Verify `setuptools.packages.find` captures everything, or add `MANIFEST.in`:
   ```
   include LICENSE
   include README.md
   include CHANGELOG.md
   recursive-include templates *.md *.yml *.example
   recursive-include agents *.md
   recursive-include docker *.sh *.env *.conf Dockerfile*
   recursive-include scripts *.sh
   recursive-include .claude/skills *.md
   ```

### Files to modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add metadata fields, dev dependencies, package data |

### Files to create

| File | Purpose |
|------|---------|
| `MANIFEST.in` | Ensure non-Python files are included in source distribution |

**Complexity:** Small (1 hour)

---

## Sub-Phase 10D: Documentation Audit

**Items:** Review docs for internal references (11), add architecture overview (12), update example project (13)

**Rationale:** Existing docs are thorough and well-written, but they were authored for internal use. A final pass ensures no internal references, broken links, or assumptions about prior context leak into the public release.

### Deliverables

1. **Audit all `docs/*.md` files** — Scan for:
   - Internal references (company names, private URLs, internal tool names)
   - Broken links (references to removed files like `docs/plans/` or `docs/reviews/`)
   - Assumptions about pre-existing knowledge
   - Stale references to `config.sh` (now `config.toml`)
   - Any reference to `StrongDM` or internal origins

2. **Create `docs/ARCHITECTURE.md`** — A contributor-oriented overview covering:
   - Information barrier design (why agents are isolated, what Docker enforces)
   - Pipeline flow diagram (spec → validate → scenarios → implement → gate → review → oracle)
   - Agent interaction model (who reads/writes what)
   - Security model summary (container boundary, tmpfs overlays, egress filtering)
   - Key modules and their responsibilities

3. **Improve `examples/rest-api/README.md`** — Make the example more standalone:
   - Include prerequisites inline (don't assume QUICKSTART was read)
   - Add expected output for each command
   - Add a "What just happened?" section explaining the pipeline stages

### Files to create

| File | Purpose |
|------|---------|
| `docs/ARCHITECTURE.md` | Contributor-oriented system design overview |

### Files to modify

| File | Change |
|------|--------|
| `docs/QUICKSTART.md` | Remove any internal references, verify all links |
| `docs/WORKFLOW.md` | Remove any internal references, verify all links |
| `docs/INTEGRATION.md` | Remove any internal references, verify all links |
| `docs/TROUBLESHOOTING.md` | Remove any internal references, verify all links |
| `docs/BEST-PRACTICES.md` | Remove any internal references, verify all links |
| `docs/TEAM-SETUP.md` | Remove any internal references, verify all links |
| `docs/TOOLS.md` | Remove any internal references, verify all links |
| `docs/ESCALATION.md` | Remove any internal references, verify all links |
| `docs/MIGRATION.md` | Remove any internal references, verify all links |
| `docs/DIGITAL-TWINS.md` | Remove any internal references, verify all links |
| `docs/DEPLOYMENT-SCRIPTS.md` | Remove any internal references, verify all links |
| `examples/rest-api/README.md` | Make standalone, add expected output, add explanation |
| `CLAUDE.md` | Remove references to removed docs/plans/ and docs/reviews/ paths |

**Complexity:** Medium (2-3 hours)

---

## Sub-Phase 10E: Final Validation & Tag

**Items:** Build verification (14), test suite pass (15), install-from-clean test (16), tag release (17)

**Rationale:** The release gate. Every previous sub-phase makes changes; this one verifies they all work together. A clean install from PyPI (or `pip install .`) on a fresh machine (or clean venv) is the ultimate test.

### Deliverables

1. **Build verification** — Run `python -m build` and confirm both sdist and wheel are produced without warnings. Verify non-Python files are present in the wheel.

2. **Full test suite pass** — `python -m pytest tests/` must pass with zero failures. No skips allowed unless documented and justified.

3. **Clean install test** — In a fresh virtualenv (Python 3.11+), run:
   ```bash
   python -m venv /tmp/df-test && source /tmp/df-test/bin/activate
   pip install .
   dark-factory --help
   dark-factory --version
   ```
   Verify CLI launches, help text is correct, version matches `pyproject.toml`.

4. **Tag and release** — After all checks pass:
   ```bash
   git tag -a v1.0.0 -m "Dark Factory v1.0.0 — initial open-source release"
   ```
   Optionally publish to PyPI: `python -m build && twine upload dist/*`

### Verification checklist

| Check | Command | Expected |
|-------|---------|----------|
| Build succeeds | `python -m build` | sdist + wheel in `dist/` |
| Tests pass | `python -m pytest tests/` | All green, 0 failures |
| CLI works from clean install | `pip install . && dark-factory --help` | Help text printed |
| No private content | `grep -r "me" . --include="*.py" --include="*.md" --include="*.json" --include="*.toml"` | Zero matches |
| No business plan | `ls docs/business-plan.md` | File not found |
| No phase plans | `ls docs/plans/` | Empty or directory not found |
| No review history | `ls docs/reviews/` | Empty or directory not found |
| LICENSE exists | `cat LICENSE` | MIT text |
| README exists | `head -5 README.md` | Project title and description |
| .gitignore covers settings | `grep "settings.local" .gitignore` | Match found |

**Complexity:** Small (1-2 hours)

---

## Dependency Graph

```
10A (Sanitize Repository)
 |
 ├── 10B (Standard OSS Files) — depends on clean repo state
 |    |
 |    └── 10C (PyPI Packaging) — depends on README.md existing
 |
 ├── 10D (Documentation Audit) — depends on knowing which files were removed in 10A
 |
 └── 10E (Final Validation) — depends on ALL previous sub-phases
```

10A must come first. 10B and 10D can run in parallel after 10A. 10C depends on 10B (needs README.md). 10E is the final gate.

---

## Implementation Order

| Sub-Phase | Scope | New Files | Modified Files |
|-----------|-------|-----------|----------------|
| **10A** | Sanitize repository | 0 | 1 (`.gitignore`) + file deletions |
| **10B** | Standard OSS files | 4 (`README.md`, `LICENSE`, `CONTRIBUTING.md`, `CHANGELOG.md`) | 1 (`pyproject.toml`) |
| **10C** | PyPI packaging | 1 (`MANIFEST.in`) | 1 (`pyproject.toml`) |
| **10D** | Documentation audit | 1 (`docs/ARCHITECTURE.md`) | 13 (all docs + example + CLAUDE.md) |
| **10E** | Final validation & tag | 0 | 0 (verification only) |
| **Total** | | **6 new files** | **~16 modified** + ~19 deleted/moved |

---

## Success Criteria

After Phase 10, a developer who has never seen Dark Factory should be able to:

1. Find the project on GitHub or PyPI and understand what it does within 30 seconds (README)
2. Install with `pip install dark-factory` (package works, no broken imports)
3. Follow QUICKSTART to first pipeline run in under 15 minutes
4. Find no personal paths, internal documents, or private strategy content anywhere in the repo
5. Contribute a fix or feature using CONTRIBUTING.md guidelines without asking questions

---

## Open Questions

| ID | Question | Impact | Decision Needed By |
|----|----------|--------|--------------------|
| Q-1 | Keep `docs/plans/` and `docs/reviews/` as archived history or remove? | Affects 10A scope and repo cleanliness | Before 10A |
| Q-2 | GitHub organization name for URLs in `pyproject.toml` and README? | Affects 10B and 10C | Before 10B |
| Q-3 | Publish to PyPI immediately or wait for community feedback? | Affects 10E scope | Before 10E |
| Q-4 | Squash git history before release to remove internal commit messages, or keep full history? | Affects credibility vs. transparency | Before 10E |
