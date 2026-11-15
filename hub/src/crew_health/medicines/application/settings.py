"""Hub settings of the medicines module (E32 11; SYSTEM 5.11): every bound named, with a basis and a ceiling."""

from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.medicines.application.use_cases import Limits

# key, minimum, maximum, basis
_INTEGERS: tuple[tuple[str, int, int, str], ...] = (
    (
        "medicines.max_window_rows",
        1,
        100_000,
        "E32 3.6 team default 1000: administrations one limit window may hold before history_too_large",
    ),
    (
        "medicines.future_tolerance_seconds",
        0,
        3600,
        "SYSTEM C6 team default 300: how far a client or administered time may lead the hub clock",
    ),
    (
        "medicines.clock_backward_tolerance_seconds",
        0,
        3600,
        "SYSTEM C2 team default 60: a stored medicines time this far past the hub clock is a discontinuity",
    ),
    (
        "medicines.clock_forward_tolerance_seconds",
        1,
        3600,
        "SYSTEM C3 team default 60: wall time running ahead of monotonic time by more than this between two "
        "medicines operations of one process is a forward clock jump",
    ),
    (
        "medicines.assessment_timeout_seconds",
        1,
        86_400,
        "E32 11 default 600: the potency child is killed at this deadline and the run is timed_out",
    ),
    (
        "medicines.worker_memory_limit_bytes",
        1,
        64 * 1024**3,
        "E32 11 default 4 GiB: a child whose peak resident memory passes this fails the run memory_limit",
    ),
    (
        "medicines.memory_sample_ms",
        10,
        60_000,
        "E32 11 default 1000: how often the child samples its peak resident memory",
    ),
    (
        "medicines.retained_unreferenced_runs",
        0,
        10_000,
        "E32 5.2 default 20: newer unreferenced runs kept with their inputs before an older run's inputs are pruned",
    ),
    (
        "medicines.max_inputs_bytes",
        1024,
        1024**3,
        "E32 11 default 128 MiB: an engine inputs document over this fails the run input_limit_exceeded",
    ),
)
CONFIG_DIR = "medicines.config_dir"
ENGINE_PYTHON = "medicines.engine_python"


def specs() -> tuple[SettingSpec, ...]:
    integers = tuple(
        SettingSpec(
            key=key,
            kind="integer",
            unit=None,
            minimum=Decimal(low),
            maximum=Decimal(high),
            max_text_bytes=None,
            basis=basis,
        )
        for key, low, high, basis in _INTEGERS
    )
    directory = SettingSpec(
        key=CONFIG_DIR,
        kind="text",
        unit=None,
        minimum=None,
        maximum=None,
        max_text_bytes=4096,
        basis="interim until ConfigName carries treatments and medicines: the configuration directory read at start",
    )
    python = SettingSpec(
        key=ENGINE_PYTHON,
        kind="text",
        unit=None,
        minimum=None,
        maximum=None,
        max_text_bytes=4096,
        basis="E32 5.3: absolute path of the interpreter that carries pharmacy and numpy; empty means no engine "
        "on this hub, and every run fails engine_unavailable",
    )
    return (*integers, directory, python)


def read_limits(settings: HubSettings) -> Limits:
    return Limits(
        max_rows=settings.integer("storage.max_rows"),
        max_window_rows=settings.integer("medicines.max_window_rows"),
        future_tolerance_seconds=settings.integer("medicines.future_tolerance_seconds"),
        clock_backward_tolerance_seconds=settings.integer("medicines.clock_backward_tolerance_seconds"),
        clock_forward_tolerance_seconds=settings.integer("medicines.clock_forward_tolerance_seconds"),
        retained_unreferenced_runs=settings.integer("medicines.retained_unreferenced_runs"),
        max_inputs_bytes=settings.integer("medicines.max_inputs_bytes"),
    )
