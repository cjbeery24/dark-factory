"""Root test conftest — makes tests/ a pytest root so both unit/ and integration/ are discovered."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_metrics_corpus(tmp_path_factory, monkeypatch):
    """Redirect the durable agent-metrics corpus to a per-session tmp dir.

    The corpus defaults to the user's real ~/.dark-factory/corpus/. The inline
    stream-summary hook (pipeline.agents.invoke_agent) appends to it on every
    non-dry-run invocation, so without this an agent-pipeline test would write
    into the developer's real home. Tests that exercise the corpus directly pass
    explicit corpus_path values and are unaffected by this env override.
    """
    corpus_dir = tmp_path_factory.mktemp("corpus_isolation")
    monkeypatch.setenv("DARK_FACTORY_CORPUS_DIR", str(corpus_dir))
