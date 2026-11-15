import pytest

from crew_health_device.application.settings import SettingsError, descriptors, load_settings


def document() -> dict[str, object]:
    return {
        item.key: {
            "value": item.minimum,
            "unit": item.unit,
            "basis": "Test operational bound",
            "to_be_set_by": "operator",
        }
        for item in descriptors()
    }


@pytest.mark.trace("AGENTS 3.2")
def test_complete_settings_document_loads_without_defaults() -> None:
    settings = load_settings(document())
    assert settings.max_frames == 1
    assert settings.connect_timeout_ms == 1


@pytest.mark.trace("AGENTS 3.2")
@pytest.mark.parametrize(
    "mutation", ["missing", "unknown", "bool", "range", "basis", "shape", "unit", "owner"]
)
def test_invalid_settings_document_fails_with_key_and_reason(mutation: str) -> None:
    doc = document()
    key = "adapter.max_frames"
    if mutation == "missing":
        del doc[key]
    elif mutation == "unknown":
        doc["adapter.unknown"] = doc[key]
    elif mutation == "shape":
        doc[key] = 1
    else:
        values = {
            "bool": ("value", True),
            "range": ("value", 4097),
            "basis": ("basis", ""),
            "unit": ("unit", "wrong"),
            "owner": ("to_be_set_by", ""),
        }
        field, value = values[mutation]
        doc[key] = {"value": 1, "unit": None, "basis": "Test bound", "to_be_set_by": "operator", field: value}
    with pytest.raises(SettingsError) as caught:
        load_settings(doc)
    assert caught.value.key
    assert caught.value.reason


@pytest.mark.trace("AGENTS 3.2")
def test_settings_entry_rejects_unknown_metadata() -> None:
    doc = document()
    doc["adapter.max_frames"] = {
        "value": 1,
        "unit": None,
        "basis": "Test bound",
        "to_be_set_by": "operator",
        "extra": "unexpected",
    }
    with pytest.raises(SettingsError, match="invalid_entry_fields"):
        load_settings(doc)
