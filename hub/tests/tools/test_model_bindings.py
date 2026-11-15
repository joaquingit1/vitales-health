"""E12 over E17's real ModelReleaseReader (ModelServices over a ManifestStore) for an installed release.

E17 refuses unusable artifacts with its own ModelRefused. Before this binding mapped them, a refused release
catalog escaped the tools binding and stopped the whole hub at startup (AGENTS rule 3)."""

# mypy: disable-error-code="type-abstract"
from __future__ import annotations

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.application.ports import ModelInstallation, ModelReleaseReader
from crew_health.model.module import register
from crew_health.model.settings import specifications
from crew_health.tools.adapters.jsonschema_validator import CatalogValidator
from crew_health.tools.adapters.model_bindings import active_release, compile_catalog, release_retrieval
from crew_health.tools.domain.errors import ToolsInvalid
from tests.tools.e12_station import MODEL_RELEASE, install_model  # type: ignore[import-not-found]

E17_VALUES = {
    "model.max_bytes": 1048576,
    "model.max_chunks": 256,
    "model.max_tools": 8,
    "model.connect_timeout_ms": 1000,
    "model.cancel_poll_ms": 10,
    "model.memory_budget_bytes": 7500000000,
    "model.cpu_budget": 6,
    "model.nice": 10,
    "model.stop_grace_ms": 1000,
}


def reader(installation: ModelInstallation) -> ModelReleaseReader:
    registry = DeclarationRegistry(max_ports=32, max_hooks=128)
    settings = MemorySettings(specifications(), E17_VALUES)
    registry.provide(HubSettings, lambda resolver: settings)
    registry.provide(ModelInstallation, lambda resolver: installation)
    register(registry)
    return registry.resolve().require(ModelReleaseReader)


@pytest.mark.trace("T-E12-011", "I7")
def test_a_release_whose_catalog_e17_refuses_leaves_the_catalog_unavailable(
    store: ManifestStore, tools_settings: MemorySettings
) -> None:
    models = reader(install_model(store, refused_catalog=True))
    active = active_release(models)
    assert active is not None
    assert active.release_id == MODEL_RELEASE
    with pytest.raises(ToolsInvalid) as raised:
        compile_catalog(models, CatalogValidator(tools_settings), active.release_id)
    assert raised.value.code == "CONFIG_INVALID"


@pytest.mark.trace("T-E12-011", "T-E12-067")
def test_a_release_e17_accepts_compiles_its_catalog_and_retrieval_settings(
    store: ManifestStore, tools_settings: MemorySettings
) -> None:
    models = reader(install_model(store))
    catalog = compile_catalog(models, CatalogValidator(tools_settings), MODEL_RELEASE)
    assert catalog.model_release_id == MODEL_RELEASE
    assert catalog.exposed("read_source_passage", "assessment")
    assert not catalog.exposed("read_source_passage", "intake")
    assert release_retrieval(models, MODEL_RELEASE).k_lexical == 20


@pytest.mark.trace("T-E12-011", "I7")
def test_an_unreadable_active_pointer_is_no_model_release(store: ManifestStore) -> None:
    installation = install_model(store)
    (store.data_dir / "models/active.json").write_text("{not json", encoding="utf-8")
    assert active_release(reader(installation)) is None


@pytest.mark.trace("T-E12-011", "I7")
def test_retrieval_settings_of_an_unknown_release_are_a_config_refusal(store: ManifestStore) -> None:
    models = reader(install_model(store))
    with pytest.raises(ToolsInvalid) as raised:
        release_retrieval(models, "model_missing")
    assert raised.value.code == "CONFIG_INVALID"
