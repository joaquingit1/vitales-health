"""Typed exceptions of the potency engine.

Every exception carries a stable machine-readable ``code`` so the hub can map it to a
response without parsing messages. Callers catch the narrowest class they can handle;
``PharmacyError`` is the root. See docs/ENGINE-SPEC.md section 1.6.
"""

from collections.abc import Sequence


class PharmacyError(Exception):
    """Root of all engine errors."""

    code: str = "pharmacy_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- Configuration ---------------------------------------------------------------------
class ConfigError(PharmacyError):
    """A configuration document cannot be used."""

    code = "config_error"


class ConfigLoadError(ConfigError):
    """The file is missing, unreadable or not valid JSON."""

    code = "config_load_error"

    def __init__(self, message: str, path: str) -> None:
        super().__init__(f"{message} ({path})")
        self.path = path


class ConfigSchemaError(ConfigError):
    """The document does not validate against its JSON Schema."""

    code = "config_schema_error"

    def __init__(self, document_id: str, errors: Sequence[str]) -> None:
        joined = "; ".join(errors)
        super().__init__(f"{document_id}: {len(errors)} schema violation(s): {joined}")
        self.document_id = document_id
        self.errors: tuple[str, ...] = tuple(errors)


class ConfigReferenceError(ConfigError):
    """A cross-document reference does not resolve (unknown source ID, product, package,
    zone, reaction class, anchor or reference study)."""

    code = "config_reference_error"

    def __init__(self, document_id: str, dangling: Sequence[str]) -> None:
        super().__init__(f"{document_id}: unresolved reference(s): {', '.join(dangling)}")
        self.document_id = document_id
        self.dangling: tuple[str, ...] = tuple(dangling)


# --- Inputs ------------------------------------------------------------------------------
class InputError(PharmacyError):
    """A runtime input (sensor series, lot record, observation, time) is invalid."""

    code = "input_error"


class SensorDataError(InputError):
    """A sensor series is malformed: unknown quantity or unit, non-finite or out-of-domain
    value, timestamps not strictly increasing, or ISO and float timestamps disagreeing."""

    code = "sensor_data_error"


class UnitError(InputError):
    """A unit is unknown or incompatible with the quantity it labels."""

    code = "unit_error"


class TimeWindowError(InputError):
    """The requested window is invalid: as_of before manufacture, custody intervals that
    overlap or leave a hole, a package history that does not start at manufacture."""

    code = "time_window_error"


class ObservationError(InputError):
    """An assay or inspection observation is invalid (unknown lot or ingredient, measured
    before manufacture, non-positive standard deviation)."""

    code = "observation_error"


# --- Chemistry -----------------------------------------------------------------------------
class StructureError(PharmacyError):
    """A SMILES or SMARTS string cannot be parsed or sanitized."""

    code = "structure_error"


# --- Models and numerics ----------------------------------------------------------------------
class ModelDomainError(PharmacyError):
    """Parameters outside a model kind's domain (for example n = 1 for kind Fn, alpha0 not
    in (0, 1) for kind B1, negative rate)."""

    code = "model_domain_error"


class ComputationError(PharmacyError):
    """A numerical procedure failed."""

    code = "computation_error"


class ConvergenceError(ComputationError):
    """An iterative procedure did not converge within its declared limits."""

    code = "convergence_error"


class XtbError(ComputationError):
    """The GFN2-xTB calculation (tblite) failed or did not converge."""

    code = "xtb_error"


# --- Reproducibility and records -----------------------------------------------------------------
class DeterminismError(PharmacyError):
    """A replay with the same pins produced a different result, or pins are incomplete."""

    code = "determinism_error"


class StoreError(PharmacyError):
    """The append-only result store was asked to overwrite or delete a record, or its hash
    chain does not verify."""

    code = "store_error"
