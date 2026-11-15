"""In-memory sourced profiles and explicitly stepped time for E07 tests."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import cast

import httpx
from jsonschema import Draft202012Validator

from crew_health_device.application.settings import AdapterSettings, descriptors, load_settings
from crew_health_device.domain.acquisition import Binding
from crew_health_device.domain.profile import DeviceProfile, parse_profile


def validate(document: Mapping[str, object], schema: str) -> None:
    resource = files("crew_health_device").joinpath("schemas", schema + ".schema.json")
    Draft202012Validator(json.loads(resource.read_text(encoding="utf-8"))).validate(document)


def profile_document() -> dict[str, object]:
    return {
        "profile_id": "plx-test",
        "version": "1",
        "family": "oximeter",
        "device_label": "Test PLX device",
        "model": None,
        "calibration": "unknown",
        "hardware_evidence": "unmeasured",
        "evidence_ref": None,
        "provenance": [
            {"path": "Bluetooth SIG PLX", "note": "Nonclinical test limits; no hardware evidence"}
        ],
        "components": [
            {
                "field": "spo2",
                "indicator_code": "spo2",
                "units": {"percent": "%"},
                "range_min": "0",
                "range_max": "100",
                "saturation_low": None,
                "saturation_high": None,
                "max_rate_per_second": None,
            }
        ],
        "min_samples": 1,
        "rejecting_status": ["invalid_measurement", "inadequate_signal"],
        "noted_status": ["validated_data", "reserved_bits"],
    }


def profile() -> DeviceProfile:
    document = profile_document()
    validate(document, "device-profile")
    return parse_profile(document)


def binding() -> Binding:
    return Binding(
        request_id="req-1",
        acquisition_id="acq-1",
        generation=1,
        patient_id="crew-1",
        episode_id=None,
        capability_code="spo2",
        method="device",
        device_id="ox-1",
        origin_mode="LIVE",
        reserved_sequence=1,
        expected_user_index=None,
    )


def settings_document() -> dict[str, object]:
    values = {
        "max_frames": 8,
        "max_frame_bytes": 512,
        "journal_max_entries": 100,
        "journal_max_bytes": 1048576,
        "profile_max_bytes": 1048576,
    }
    return {
        d.key: {
            "value": values.get(d.key.removeprefix("adapter."), min(1000, d.maximum)),
            "unit": d.unit,
            "basis": "E07 deterministic test bounds",
            "to_be_set_by": "test operator",
        }
        for d in descriptors()
    }


def settings() -> AdapterSettings:
    return load_settings(settings_document())


class Clock:
    def __init__(
        self,
        steps: tuple[int, ...] = (
            0,
            1000000,
            2000000,
            3000000,
            4000000,
            5000000,
            6000000,
            7000000,
            8000000,
            9000000,
            10000000,
            11000000,
            12000000,
            13000000,
            14000000,
            15000000,
        ),
    ) -> None:
        self.steps = iter(steps)
        self.value = 0

    def now(self) -> datetime:
        return datetime(2026, 11, 14, tzinfo=UTC) + timedelta(microseconds=self.value // 1000)

    def monotonic_ns(self) -> int:
        self.value = next(self.steps)
        return self.value


def component_document(document: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], document["components"])[0]


class ASGIClient:
    """Synchronous test facade over the nondeprecated httpx ASGI transport."""

    def __init__(self, app: object) -> None:
        from fastapi import FastAPI

        if not isinstance(app, FastAPI):
            raise TypeError("fastapi_required")
        self.app = app

    def __enter__(self) -> ASGIClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        pass

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        return self._request("GET", path, None, headers)

    def post(
        self, path: str, *, json: object = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return self._request("POST", path, json, headers)

    def _request(
        self, method: str, path: str, body: object, headers: dict[str, str] | None
    ) -> httpx.Response:
        import asyncio

        async def perform() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1"
            ) as client:
                return await client.request(method, path, json=body, headers=headers)

        return asyncio.run(perform())
