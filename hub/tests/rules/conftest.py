"""E24 pure-domain test fixtures. Documents come from configuration, never from hand-typed clinical text."""

from __future__ import annotations

from collections.abc import Callable
from typing import Unpack

import pytest

from crew_health.rules.domain.definitions import RulesDocument, parse_rules_document  # noqa: E402
from crew_health.rules.domain.fingerprint import ReleaseDocuments  # noqa: E402

from . import rules_harness as h  # noqa: E402
from .e24_station import patched  # noqa: E402, F401  (the real-station fixture, shared by the integration tests)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "E24: warnings engine")


@pytest.fixture(scope="session")
def deployment_doc() -> RulesDocument:
    """The deployment `config/warning-rules.json`, parsed."""
    return parse_rules_document(h.deployment_rules())


@pytest.fixture(scope="session")
def example_doc() -> RulesDocument:
    """The package example document, a labeled test profile only."""
    return parse_rules_document(h.example_value("warning-rules"))


@pytest.fixture(scope="session")
def release_docs() -> ReleaseDocuments:
    return h.release_documents()


@pytest.fixture
def world(deployment_doc: RulesDocument, release_docs: ReleaseDocuments) -> h.World:
    return h.World(deployment_doc, release_docs)


@pytest.fixture
def make_world(release_docs: ReleaseDocuments) -> Callable[..., h.World]:
    def build(doc: RulesDocument, **kwargs: Unpack[h.WorldKw]) -> h.World:
        return h.World(doc, release_docs, **kwargs)

    return build
