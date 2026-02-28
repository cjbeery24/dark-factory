# Quick Start

Get Dark Factory running in your project in 5 steps.

---

## 1. Integrate

```bash
/path/to/dark-factory/scripts/integrate.sh /path/to/your/project
```

## 2. Review config

Open `.dark-factory/config.sh` and verify the auto-detected commands match your project.

## 3. Write a spec

```bash
cp dark-factory/templates/feature-spec.md .dark-factory/specs/my-feature.md
# Edit the spec with your feature details
```

## 4. Run the pipeline

```bash
./dark-factory/scripts/dark-factory.sh my-feature
```

The pipeline will: validate your spec, generate blind test scenarios, implement the feature, run quality gates, review the code, and make a ship/no-ship decision.

## 5. Ship it

If the Oracle says SHIP, your code is ready. Review the outputs:
- Implementation: your source files
- Quality report: `.dark-factory/reports/my-feature-quality-gate.md`
- Review: `.dark-factory/reviews/my-feature-review.md`

---

## What just happened?

The pipeline ran 5 agents in sequence with **information barriers**:

1. **Spec Validator** checked your spec for completeness
2. **Scenario Architect** generated blind tests from the spec (never saw the code)
3. **Implementer** wrote code from the spec (never saw the blind tests)
4. **Quality Gate** ran build, lint, type check, self-tests, and blind scenarios
5. **Reviewer + Oracle** evaluated quality and made the ship decision

If it failed, the pipeline iterates (up to 3 times) with feedback.

---

## Next steps

- See all spec templates: `ls dark-factory/templates/`
- Read the full workflow: `dark-factory/docs/WORKFLOW.md`
- Integration details: `dark-factory/docs/INTEGRATION.md`
- Team setup: `dark-factory/docs/TEAM-SETUP.md`
