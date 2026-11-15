from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity, Unit
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError

from crew_health_bench.adapters.documents import decode_json, load_document, read_json
from crew_health_bench.contracts_gen.case import CaseDocument, CaseQuantity, RevealTableDocument, VisiblePart
from crew_health_bench.contracts_gen.hidden import HiddenPart
from crew_health_bench.domain.cases import (
    CapabilityMatch,
    Case,
    CaseKey,
    CaseReview,
    ConfigPrecondition,
    QuestionMatch,
    RevealTable,
    SimAnswer,
    SimMeasurement,
    Suite,
)
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.labels import LabelConstraint, label_constraints, require_independent_labels


@dataclass(frozen=True, slots=True)
class LoadedCase:
    document: CaseDocument
    case: Case
    visible: VisiblePart


def _quantity(value: CaseQuantity | None) -> Quantity | None:
    return None if value is None else Quantity(Decimal(str(value.value)), Unit(value.unit))


def _reveal(value: RevealTableDocument | None) -> RevealTable | None:
    if value is None:
        return None
    questions = tuple(
        QuestionMatch(
            tuple(row.any_of),
            tuple(row.none_of),
            SimAnswer(row.answer.state, _quantity(row.answer.value)),
        )
        for row in value.questions
    )
    capabilities = tuple(
        CapabilityMatch(
            row.capability_code,
            SimMeasurement(row.answer.outcome, _quantity(row.answer.value), row.answer.reason),
        )
        for row in value.capabilities
    )
    if len({row.capability_code for row in capabilities}) != len(capabilities):
        raise BenchError("case_invalid")
    return RevealTable(questions, capabilities)


class CaseDocuments:
    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int,
        ids: IdFactory,
        label_constraints: tuple[LabelConstraint, ...],
    ) -> None:
        self.maximum, self.ids, self.label_constraints = max_file_bytes, ids, label_constraints
        self.validators: dict[str, Draft202012Validator] = {}
        for name in ("case", "hidden-part"):
            schema = read_json(root / (name + ".schema.json"), max_file_bytes=max_file_bytes)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as error:
                raise BenchError("case_invalid") from error
            self.validators[name] = Draft202012Validator(schema)

    @classmethod
    def from_config(cls, config_dir: Path, *, max_file_bytes: int, ids: IdFactory) -> CaseDocuments:
        """The parser with the `label_constraints` of `suites.json`, loaded and schema checked (design 3.2)."""
        suites = load_document(config_dir / "suites.json", max_file_bytes=max_file_bytes)
        return cls(
            config_dir.parent / "schemas",
            max_file_bytes=max_file_bytes,
            ids=ids,
            label_constraints=label_constraints(suites),
        )

    def _validate(self, name: str, value: object) -> None:
        try:
            self.validators[name].validate(value)
        except SchemaValidationError as error:
            raise BenchError("case_invalid") from error

    def load(self, path: Path) -> LoadedCase:
        return self.parse_mapping(read_json(path, max_file_bytes=self.maximum))

    def parse(self, data: bytes) -> LoadedCase:
        if len(data) > self.maximum:
            raise BenchError("case_invalid")
        return self.parse_mapping(decode_json(data))

    def parse_mapping(self, value: Mapping[str, object]) -> LoadedCase:
        self._validate("case", value)
        try:
            document = CaseDocument.model_validate(value, strict=True)
            visible = canonical_json(document.visible.model_dump(mode="json"))
            if document.case_id != self.ids.derived("benchcase", sha256(visible).hexdigest()):
                raise BenchError("case_invalid")
            if document.visible.canary != document.canary or (
                document.revealable is not None and document.revealable.canary != document.canary
            ):
                raise BenchError("case_invalid")
            key = document.key
            case = Case(
                document.case_id,
                Suite(document.suite),
                CaseKey(key.family_id, key.source_vignette_id, key.patient_id, key.episode_id),
                document.origin,
                document.derived_from,
                tuple(document.condition_ids),
                tuple(
                    ConfigPrecondition(row.document, row.pointer, row.must_be) for row in document.requires
                ),
                visible.decode("utf-8"),
                _reveal(document.revealable),
                document.hidden_sha256,
                document.canary,
                CaseReview(document.review.state, tuple(document.review.reviewer_roles)),
            )
            return LoadedCase(document, case, document.visible)
        except (ValidationError, ProblemError, ValueError) as error:
            raise BenchError("case_invalid") from error

    def load_hidden(self, path: Path, *, family_id: str) -> HiddenPart:
        return self.parse_hidden(read_json(path, max_file_bytes=self.maximum), family_id=family_id)

    def parse_hidden(self, value: Mapping[str, object], *, family_id: str) -> HiddenPart:
        self._validate("hidden-part", value)
        try:
            hidden = HiddenPart.model_validate(value, strict=True)
        except ValidationError as error:
            raise BenchError("case_invalid") from error
        require_independent_labels(
            hidden.source.text,
            tuple(hidden.source.source_ids),
            hidden.expected_urgency,
            family_id=family_id,
            constraints=self.label_constraints,
        )
        return hidden

    def hidden_for(self, case: LoadedCase, path: Path) -> tuple[HiddenPart, tuple[str, ...]]:
        from crew_health_bench.adapters.isolation import vault_path

        path = vault_path(path.parent, path.name)
        try:
            with path.open("rb") as stream:
                raw = stream.read(self.maximum + 1)
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
        if len(raw) > self.maximum or sha256(raw).hexdigest() != case.case.hidden_sha256:
            raise BenchError("integrity", exit_code=6)
        hidden = self.parse_hidden(decode_json(raw), family_id=case.case.key.family_id)
        if hidden.canary != case.case.canary:
            raise BenchError("case_invalid")
        parts = [case.case.visible]
        if case.document.revealable is not None:
            parts.append(case.document.revealable.model_dump_json())
        parts.append(raw.decode("utf-8"))
        return hidden, tuple(parts)
