"""Body weight from records (E32 3.5 step 2): the subject's latest accepted `mass` observations in this mission and
mode, read through a structural copy of records' store seam (`RecordsStore.observations`; a signature test keeps the
copy equal to the original). An observation in a unit that is not a mass, or without an observed time, is skipped:
weight stays absent rather than guessed, and a per-kilogram rule refuses `weight_missing`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.medicines.domain.model import WeightObservation

MASS_CODE = "mass"  # docs/product/input-dictionary.json field_id "mass", unit kg
_TO_KG = {
    "kg": Decimal(1),
    "g": Decimal("0.001"),
    "[lb_av]": Decimal("0.45359237"),
}  # UCUM; the pound is exact


class _Unit(Protocol):
    @property
    def code(self) -> str: ...


class _Quantity(Protocol):
    @property
    def value(self) -> Decimal: ...
    @property
    def unit(self) -> _Unit: ...


class _Normalized(Protocol):
    @property
    def original_at(self) -> datetime | None: ...
    @property
    def corrected_at(self) -> datetime | None: ...


class Observation(Protocol):
    """The fields of records' ObservationRecord this reader uses."""

    @property
    def observation_id(self) -> str: ...
    @property
    def quantity(self) -> _Quantity: ...
    @property
    def normalized(self) -> _Normalized: ...


class RecordsObservations(Protocol):
    """Structural copy of `crew_health.records.application.seams.RecordsStore.observations` (records' seams module
    imports records' domain, so medicines may not import it; a signature test keeps the copy equal)."""

    def observations(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        mission_id: str,
        origin_mode: str,
        code: str,
        limit: int,
        accepted_only: bool = True,
        before: datetime | None = None,
        source: tuple[str, str | None] | None = None,
        states: tuple[str, ...] | None = None,
        corrected_only: bool = False,
    ) -> tuple[Observation, ...]: ...


class RecordsWeightReader:
    """Implements medicines.application.ports.WeightReader over records' store."""

    def __init__(self, store: RecordsObservations) -> None:
        self.store = store

    def weights(
        self,
        view: ReadSnapshot | UnitOfWork,
        person_id: str,
        *,
        mission_id: str,
        origin_mode: str,
        limit: int,
    ) -> tuple[WeightObservation, ...]:
        rows = self.store.observations(
            view,
            person_id=person_id,
            mission_id=mission_id,
            origin_mode=origin_mode,
            code=MASS_CODE,
            limit=limit,
            accepted_only=True,
        )
        out: list[WeightObservation] = []
        for row in rows:
            factor = _TO_KG.get(row.quantity.unit.code)
            at = row.normalized.corrected_at or row.normalized.original_at
            if factor is None or at is None:
                continue
            out.append(WeightObservation(row.observation_id, row.quantity.value * factor, at))
        return tuple(sorted(out, key=lambda w: w.at, reverse=True))
