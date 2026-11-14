"""Fixtures of the chemistry unit tests (B2).

* Fast tests never run xTB: an autouse guard replaces the xTB computation with a failure
  unless the test carries the ``xtb`` marker, so a cache miss in a fast test is loud.
* ``mutate_bundle`` builds a schema-validated bundle with edited documents (never touching
  config/pharmacy).
"""

import copy
from collections.abc import Mapping

import pytest

from pharmacy.chemistry import bde
from pharmacy.config import ConfigBundle, document_from_json
from tests.unit.chemistry.support import BundleMutator, Mutation


@pytest.fixture(autouse=True)
def _no_xtb_in_fast_tests(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("xtb") is None:

        def refuse(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("a fast test reached xTB (BDE cache miss); mark it xtb or use cached sites")

        monkeypatch.setattr(bde, "_compute", refuse)


@pytest.fixture
def mutate_bundle(bundle: ConfigBundle) -> BundleMutator:
    """``mutate_bundle({"formulary": fn})`` deep-copies each named document, applies ``fn``
    to its JSON data, re-validates it against its schema and returns a new bundle."""

    def build(changes: Mapping[str, Mutation]) -> ConfigBundle:
        documents = dict(bundle.documents)
        for member, mutation in changes.items():
            data = copy.deepcopy(bundle.documents[member].data)
            mutation(data)
            documents[member] = document_from_json(data, f"test:{member}")
        return ConfigBundle(documents=documents)

    return build
