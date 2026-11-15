import json
import math
import statistics
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health_sim_clinical.physiology import Physiology, decimal_map, object_map, quantize, read_document
from crew_health_sim_clinical.scenarios import load_scenarios

D = Decimal


@pytest.mark.trace("T-E07-SIM-13", "E07-03")
def test_seeded_statistics() -> None:
    model = Physiology(72)
    p = model.parameters
    hr = [float(model.heart_rate(D(i) / 4)) for i in range(600)]
    wave = [
        math.sin(math.tau * i / 4 * float(p["heart_rate"]["respiration_per_min"]) / 60) for i in range(600)
    ]
    assert statistics.stdev(hr) > 0
    assert statistics.correlation(hr, wave) > 0.8
    spo2 = [float(model.spo2()) for _ in range(2000)]
    assert abs(statistics.stdev(spo2) - float(p["spo2"]["sensor_noise_sd_percent"])) < 0.15
    bp = [model.blood_pressure() for _ in range(2000)]
    correlation = statistics.correlation([float(row[0]) for row in bp], [float(row[1]) for row in bp])
    assert abs(correlation - float(p["blood_pressure"]["correlation"])) < 0.1


@pytest.mark.trace("T-E07-SIM-13", "E07-03")
def test_an_unsourced_temperature_amplitude_leaves_the_daily_term_out(tmp_path: Path) -> None:
    """N22 supports a rhythm, N23 p. 9 gives its acrophase, nothing read gives its amplitude: null, no swing."""
    assert read_document("physiology.json")["temperature"]["amplitude_cel"] is None  # type: ignore[index]
    flat = Physiology(5)
    flat.parameters["temperature"]["noise_sd_cel"] = D(0)
    assert {flat.temperature(datetime(2026, 1, 1, hour, tzinfo=UTC)) for hour in range(24)} == {D(37)}
    doc = read_document("physiology.json")
    temperature = object_map(doc["temperature"])
    temperature["amplitude_cel"] = "1"
    doc["temperature"] = temperature
    path = tmp_path / "physiology.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    model = Physiology(72, path=path)
    temperatures = [
        statistics.mean(float(model.temperature(datetime(2026, 1, 1, hour, tzinfo=UTC))) for _ in range(100))
        for hour in range(24)
    ]
    peak = temperatures.index(max(temperatures))
    assert abs(peak - int(model.parameters["temperature"]["acrophase_hour"])) <= 1


@pytest.mark.trace("T-E07-SIM-14", "E07-03")
def test_configuration_changes_values_and_clamps(tmp_path: Path) -> None:
    doc = read_document("physiology.json")
    spo2 = object_map(doc["spo2"])
    spo2["mean_percent"] = "200"
    spo2["sensor_noise_sd_percent"] = "0"
    doc["spo2"] = spo2
    hr = object_map(doc["heart_rate"])
    hr.update(mean_bpm="80", noise_sd_bpm="0", rsa_amplitude_bpm="0")
    doc["heart_rate"] = hr
    temperature = object_map(doc["temperature"])
    temperature.update(mesor_cel="40", amplitude_cel="0", noise_sd_cel="0")
    doc["temperature"] = temperature
    bp = object_map(doc["blood_pressure"])
    bp.update(systolic_mean_mmhg="150", diastolic_mean_mmhg="90", systolic_sd_mmhg="0", diastolic_sd_mmhg="0")
    doc["blood_pressure"] = bp
    path = tmp_path / "physiology.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    model = Physiology(1, path=path)
    assert model.spo2() == 100
    assert model.heart_rate(D(0)) == 80
    assert model.temperature(datetime(2026, 1, 1, tzinfo=UTC)) == 40
    assert model.blood_pressure() == (D(150), D(90), D(110))
    model.parameters["spo2"]["mean_percent"] = D(-100)
    assert model.spo2() == 0
    assert Physiology(1).heart_rate(D(0)) != 80
    assert quantize(D("36.85"), "0.1") == D("36.8")
    assert quantize(D("36.95"), "0.1") == D("37.0")
    assert quantize(D("64.5")) == 64
    assert quantize(D("65.5")) == 66


@pytest.mark.trace("T-E07-SIM-15", "E07-03")
@pytest.mark.parametrize("value", [[], {1: "a"}, {"p": 1}, {"p": "NaN"}, {"p": "bogus"}, {"mean_bpm": None}])
def test_invalid_parameter_objects(value: object) -> None:
    with pytest.raises(ValueError):
        decimal_map(value)


@pytest.mark.trace("T-E07-SIM-16", "E07-03")
@pytest.mark.parametrize("field,value", [("correlation", "1.1"), ("systolic_sd_mmhg", "-1")])
def test_invalid_physiology(tmp_path: Path, field: str, value: str) -> None:
    doc = read_document("physiology.json")
    bp = object_map(doc["blood_pressure"])
    bp[field] = value
    doc["blood_pressure"] = bp
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        Physiology(1, path=path)


@pytest.mark.trace("T-E07-SIM-17", "E07-03")
def test_scenario_configuration(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.json"
    document = {"scenarios": {"normal": {"description": "Changed label", "parameters": {"samples": "8"}}}}
    path.write_text(json.dumps(document), encoding="utf-8")
    scenario = load_scenarios(path)["normal"]
    assert scenario.description == "Changed label"
    assert scenario.parameters["samples"] == D(8)
    assert len(load_scenarios()) == 11
    with pytest.raises(TypeError):
        scenario.parameters["samples"] = D(3)  # type: ignore[index]


@pytest.mark.trace("T-E07-SIM-18", "E07-03")
@pytest.mark.parametrize(
    "description,parameters",
    [
        ("", {}),
        (1, {}),
        ("ok", {"samples": "0"}),
        ("ok", {"notifications": "1.5"}),
        ("ok", {"percent": "101"}),
        ("ok", {"noise_multiplier": "-1"}),
        ("ok", {"from_notification": "-1"}),
        ("ok", {"from_notification": "1.5"}),
    ],
)
def test_reject_invalid_scenarios(tmp_path: Path, description: object, parameters: dict[str, str]) -> None:
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps({"scenarios": {"bad": {"description": description, "parameters": parameters}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_scenarios(path)


@pytest.mark.trace("T-E07-SIM-19", "E07-03")
def test_missing_or_corrupt_config(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        Physiology(1, path=path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        Physiology(1, path=path)


@pytest.mark.trace("T-E07-SIM-SRC", "E07-03")
def test_every_physiology_parameter_names_its_source_or_says_it_is_an_assumption() -> None:
    from importlib.resources import files

    document = json.loads(
        files("crew_health_sim_clinical").joinpath("data", "physiology.json").read_text(encoding="utf-8")
    )
    covered = " ".join(entry["path"] for entry in document["provenance"])
    for group in ("heart_rate", "spo2", "temperature", "blood_pressure"):
        for key in document[group]:
            assert f"{group}.{key}" in covered
    notes = [entry["note"] for entry in document["provenance"]]
    assert all(
        ("NTRS" in n or "C08" in n or "RT2" in n or "RT3" in n or "MedlinePlus" in n or "assumption" in n)
        for n in notes
    )
    assert "TO_BE_SOURCED" not in json.dumps(document)
