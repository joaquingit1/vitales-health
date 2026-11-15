"""Hub settings of the supplies module (E32 11; SYSTEM 5.11): every bound named, with a basis and a ceiling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec

# key, minimum, maximum, basis
_SPECS: tuple[tuple[str, int, int, str], ...] = (
    ("supplies.max_quantity", 1, 1_000_000_000, "E32 11 team default 1000000: largest amount in one use"),
    ("supplies.max_quantity_scale", 0, 12, "E32 11 team default 6: decimal places of a quantity"),
    ("supplies.max_supplies", 1, 100_000, "team default 1024: supplies one release and one ledger read hold"),
    (
        "supplies.cover_batch_rows",
        1,
        100_000,
        "SYSTEM 8.1 batch bound for SupplyCoverFacts.rows, team default 1000",
    ),
    ("supplies.max_step_uses", 1, 1024, "team default 64: supply needs one plan step may debit"),
    ("supplies.release_cache", 1, 64, "team default 4: parsed releases kept in memory"),
    ("supplies.verify_batch_rows", 1, 100_000, "E32 5.1 team default 1000: rows per start-up verifier read"),
    (
        "supplies.verify_max_batches",
        1,
        1_000_000,
        "team default 1000: verifier reads per partition; longer is broken",
    ),
    (
        "supplies.reference_timeout_ms",
        1,
        60_000,
        "team default 500: bound on a help or plan run reference read",
    ),
    ("supplies.reference_inflight", 1, 64, "team default 4: reference reads in flight at once"),
)


def specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(
            key=key,
            kind="integer",
            unit=None,
            minimum=Decimal(low),
            maximum=Decimal(high),
            max_text_bytes=None,
            basis=basis,
        )
        for key, low, high, basis in _SPECS
    )


@dataclass(frozen=True, slots=True)
class SuppliesLimits:
    max_quantity: Decimal
    max_quantity_scale: int
    max_supplies: int
    cover_batch_rows: int
    max_step_uses: int
    release_cache: int
    verify_batch_rows: int
    verify_max_batches: int
    reference_timeout_ms: int
    reference_inflight: int
    page_rows: int  # E01's storage.max_rows: no single ledger read asks for more rows than this

    @classmethod
    def read(cls, settings: HubSettings) -> SuppliesLimits:
        def value(name: str) -> int:
            return settings.integer("supplies." + name)

        return cls(
            max_quantity=Decimal(value("max_quantity")),
            max_quantity_scale=value("max_quantity_scale"),
            max_supplies=value("max_supplies"),
            cover_batch_rows=value("cover_batch_rows"),
            max_step_uses=value("max_step_uses"),
            release_cache=value("release_cache"),
            verify_batch_rows=value("verify_batch_rows"),
            verify_max_batches=value("verify_max_batches"),
            reference_timeout_ms=value("reference_timeout_ms"),
            reference_inflight=value("reference_inflight"),
            page_rows=settings.integer("storage.max_rows"),
        )
