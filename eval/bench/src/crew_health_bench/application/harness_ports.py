"""Structural copies of the E21 harness interfaces the product-suite driver needs (design section 2, 4.5,
5.6).

E21 owns these (`eval/runner`, package `crew_health_harness`, draft PR 38). Its design section 2 declares
`EvalHubLauncher.start(profile) -> RunningHub`, `HubClient.call(operation_id, *, path_params, query, body,
idempotency_key, session, timeout) -> ApiResponse`, `SeedLoader.load(steps, ctx) -> SeedResult`,
`SimMeasurementAdapter.post_attempt(request_id, outcome, *, session) -> AttemptRef` and `ModelGate.mark(label)
-> int` with `ModelCallLog.requests_between(start_mark, end_mark)`. E18 may not import an E21 module
(R-E18-3), so these Protocols restate the declared shapes, with these recorded narrowings:

- `HubCall.call` takes and returns wire JSON objects; E21's client converts its generated request and response
  models at its own boundary. Sessions are bearer tokens (E21's `SessionToken.token`).
- `EvaluationHub.seed` stands for `SeedLoader.load` over E21's own `SeedContext`, which E18 cannot construct.
- `SimMeasurementAdapter` is declared in E21's design but absent from PR 38; its `AttemptRef` is the attempt
  ID.
- `ProductHarness.start` takes the run ID: E21 builds its launcher and evaluation marker per run, and the
  driver checks that marker (INV-E18-10). `version` is the harness version the run manifest records (R22).

The driver reads nothing else from E21; an adapter that binds E21's classes to these Protocols lives with
E21's harness once it lands (`adapters/providers.py`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from crew_health.kernel.units import Quantity

from crew_health_bench.domain.cases import SimMeasurement


@dataclass(frozen=True, slots=True)
class RunningHub:
    """E21 `launcher.RunningHub`."""

    data_root: Path
    base_url: str
    node_id: str
    profile: str


@dataclass(frozen=True, slots=True)
class HubResponse:
    """E21 `ApiResponse` with the body as the wire JSON object."""

    status_code: int
    body: Mapping[str, object] | None
    etag: str | None


@dataclass(frozen=True, slots=True)
class HubCredentials:
    """What E21's bootstrap hands the driver for `createSession` (design 6.1): the SIM persona and the
    operator."""

    persona_id: str
    crew_credential: str
    operator_credential: str


class HubCall(Protocol):
    """E21 `HubClient.call`."""

    def call(
        self,
        operation_id: str,
        *,
        path_params: Mapping[str, str],
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        idempotency_key: str | None,
        session: str | None,
        timeout: Quantity,
    ) -> HubResponse: ...


class ModelGate(Protocol):
    """E21 `ModelGate.mark` and `ModelCallLog.requests_between`; E18 only counts the recorded requests."""

    def mark(self, label: str) -> int: ...
    def requests_between(self, start_mark: int, end_mark: int) -> Sequence[object]: ...


class SimMeasurementAdapter(Protocol):
    """E21 `SimMeasurementAdapter.post_attempt`: posts a SIM attempt with the adapter token; returns its
    ID."""

    def post_attempt(self, request_id: str, outcome: SimMeasurement, *, session: str) -> str: ...


class EvaluationHub(Protocol):
    """One fresh evaluation hub as E21's launcher starts it, with its client, recorder and SIM adapter."""

    @property
    def hub(self) -> RunningHub: ...
    @property
    def client(self) -> HubCall: ...
    @property
    def gate(self) -> ModelGate: ...
    @property
    def sim(self) -> SimMeasurementAdapter: ...
    @property
    def credentials(self) -> HubCredentials: ...
    def seed(self, steps: Sequence[Mapping[str, object]]) -> int: ...


class ProductHarness(Protocol):
    """E21 `EvalHubLauncher` plus what a fresh hub needs; `stop` discards the hub and its data directory."""

    @property
    def version(self) -> str: ...
    def mounted_operations(self) -> tuple[str, ...]: ...
    def start(self, profile: str, *, run_id: str) -> EvaluationHub: ...
    def stop(self, hub: EvaluationHub) -> None: ...
