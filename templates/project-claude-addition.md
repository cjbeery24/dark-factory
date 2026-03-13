## Dark Factory — Specification-Driven Development

This project uses the **Dark Factory** pipeline for specification-driven development with enforced information barriers between AI agents.

### Critical Rules — NEVER Violate

1. **NEVER read `.dark-factory/scenarios/` when implementing code.** These contain blind test scenarios. Reading them invalidates the entire testing process. If you are writing or modifying implementation code, you MUST NOT read, reference, or reason about any file in `.dark-factory/scenarios/`. VIOLATIONS INVALIDATE THE PIPELINE RUN.

2. **NEVER read implementation source code when generating test scenarios.** Scenarios must derive purely from the specification. If you are generating scenarios, you MUST NOT read source files in `__SOURCE_DIR__/`. VIOLATIONS INVALIDATE THE PIPELINE RUN.

3. **All non-trivial changes MUST start with a validated specification** in `.dark-factory/specs/`.

4. **Quality gates are mandatory.** Code does not ship until it passes blind scenarios and review.

### Agent Quick Reference

| Role | You Read | You NEVER Read | What You Do |
|------|----------|----------------|-------------|
| Spec Validator | `.dark-factory/specs/` | — | Validate spec completeness |
| Implementer | Validated spec, source code | `.dark-factory/scenarios/` | Write code + self-tests |
| Scenario Architect | Validated spec | Source code (`__SOURCE_DIR__/`) | Generate blind test scenarios |
| Reviewer | Spec, code, test results | — | Evaluate quality |
| Oracle | Everything | — | Ship/no-ship decision |

### Directory Structure

```
.dark-factory/
├── specs/       → Specifications (input to pipeline)
├── scenarios/   → Blind test scenarios (RESTRICTED)
├── reviews/     → Review outputs
├── reports/     → Quality gate reports
├── audit/       → Audit logs (agent activity, barrier violation detection)
├── state/       → Pipeline state files (for resume/recovery)
├── plans/       → Project plans (decomposed into specs)
└── config.toml  → Project-specific toolchain config
```

### Running the Pipeline

```bash
# Full pipeline (validates spec → generates scenarios → implements → tests → reviews)
dark-factory run <spec-name>

# Individual steps
dark-factory validate <spec-name>
dark-factory gate <spec-name>
dark-factory scenarios <spec-name>
```

### Agent Definitions

See `dark-factory/agents/` for detailed agent role definitions:
- `implementer.md` — Implementation agent rules
- `scenario-architect.md` — Scenario generation rules
- `spec-validator.md` — Specification validation rules
- `reviewer.md` — Code review rules
- `oracle.md` — Ship/no-ship decision rules
