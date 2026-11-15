from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName
from crew_health.contracts_gen.schemas.capability_schema import Capability
from crew_health.contracts_gen.schemas.config_assistant_policy_schema import AssistantPolicyJson
from crew_health.contracts_gen.schemas.model_action_schema import ModelAction
from crew_health.kernel.config import CandidateRelease, Finding
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.tools.adapters.registry import tool_names
from crew_health.tools.domain.arguments import JsonBounds, parse_json
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.output_policy import require_policy_rules
from crew_health.tools.domain.phase import Phase
from crew_health.tools.settings import argument_bounds


@dataclass(frozen=True, slots=True)
class ActionValidator:
    bounds: JsonBounds

    def parse(self, raw: bytes) -> ModelAction:
        value = parse_json(raw, self.bounds)
        try:
            return ModelAction.model_validate(value, strict=True)
        except ValidationError as error:
            raise ToolsInvalid("MALFORMED_MODEL_OUTPUT") from error


class PolicyValidator:
    def __init__(self, bounds: JsonBounds | None = None) -> None:
        self.bounds = bounds

    def bind(self, settings: HubSettings) -> None:
        if self.bounds is not None:
            raise ToolsInvalid("CONFIG_INVALID")
        self.bounds = argument_bounds(settings)

    @property
    def check_id(self) -> str:
        return "tools.assistant-policy"

    def parse(self, raw: bytes) -> AssistantPolicyJson:
        if self.bounds is None:
            raise ToolsInvalid("CONFIG_INVALID")
        try:
            policy = AssistantPolicyJson.model_validate(parse_json(raw, self.bounds), strict=True)
        except ValidationError as error:
            raise ToolsInvalid("CONFIG_INVALID") from error
        require_policy_rules(tuple(rule.id.root for rule in policy.forbidden_output))
        return policy

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        document = candidate.documents.get(ConfigName.ASSISTANT_POLICY)
        if document is not None:
            try:
                self.parse(document.raw)
                return ()
            except ToolsInvalid:
                pass
        return (Finding(self.check_id, ConfigName.ASSISTANT_POLICY, "/forbidden_output", "CONFIG_INVALID"),)


@dataclass(frozen=True, slots=True)
class _ToolSchema:
    stage: Phase
    effect: Literal["read", "compute", "proposal"]
    validator: Draft202012Validator


class CompiledCatalog:
    def __init__(
        self,
        model_release_id: str,
        tools: Mapping[str, _ToolSchema],
        bounds: JsonBounds,
        result_bounds: JsonBounds,
    ) -> None:
        require_id(model_release_id)
        self._model_release_id = model_release_id
        self._tools = dict(tools)
        self._bounds = bounds
        self._result_bounds = result_bounds

    @property
    def model_release_id(self) -> str:
        return self._model_release_id

    def effect(self, name: str) -> Literal["read", "compute", "proposal"] | None:
        tool = self._tools.get(name)
        return None if tool is None else tool.effect

    def exposed(self, name: str, phase: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.stage == phase

    def parse(self, name: str, raw: bytes) -> Mapping[str, object]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        parsed = parse_json(raw, self._bounds)
        if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
            raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")
        try:
            tool.validator.validate(parsed)
        except SchemaValidationError as error:
            raise ToolsInvalid("MALFORMED_MODEL_OUTPUT") from error
        return {key: value for key, value in parsed.items()}

    def validate_result(self, name: str, raw: bytes) -> Mapping[str, object]:
        value = parse_json(raw, self._result_bounds)
        if name != "get_capabilities":
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if not isinstance(value, dict) or set(value) != {"items"} or not isinstance(value["items"], list):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        try:
            for item in value["items"]:
                Capability.model_validate(item, strict=True)
        except ValidationError as error:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED") from error
        return {"items": value["items"]}


def _scope_fields() -> frozenset[str]:
    return frozenset(
        {
            "actor",
            "authenticated_actor",
            "authorized_patient",
            "subject_person_id",
            "person_id",
            "patient_id",
            "episode_id",
            "consent",
            "evidence_release_id",
            "config_release_id",
            "model_release_id",
            "mission_id",
            "origin_mode",
            "generation",
            "source_revision",
            "session",
            "scope",
        }
    )


def _closed_schema(schema: dict[str, object], maximum: int) -> None:
    pending: list[object] = [schema]
    scopes = _scope_fields()
    keywords = {
        "$schema",
        "$id",
        "type",
        "title",
        "description",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
    }
    for _ in range(maximum):
        if not pending:
            return
        node = pending.pop()
        if not isinstance(node, dict) or set(node) - keywords:
            raise ToolsInvalid("CONFIG_INVALID")
        kind = node.get("type")
        if not isinstance(kind, str) or kind not in {
            "object",
            "array",
            "string",
            "number",
            "integer",
            "boolean",
            "null",
        }:
            raise ToolsInvalid("CONFIG_INVALID")
        if kind == "object":
            properties = node.get("properties")
            if node.get("additionalProperties") is not False or not isinstance(properties, dict):
                raise ToolsInvalid("CONFIG_INVALID")
            if scopes.intersection(properties):
                raise ToolsInvalid("CONFIG_INVALID")
            pending.extend(properties.values())
        elif kind == "array":
            pending.append(node.get("items"))
    raise ToolsInvalid("CONFIG_INVALID")


class CatalogValidator:
    def __init__(self, settings: HubSettings) -> None:
        self._bounds = argument_bounds(settings)
        self._max_bytes = int(settings.quantity("tools.max_schema_bytes").value)
        self._result_bounds = JsonBounds(
            int(settings.quantity("tools.max_result_bytes").value),
            self._bounds.max_depth,
            self._bounds.max_array_items,
            self._bounds.max_string_chars,
        )
        self._max_handlers = settings.integer("tools.max_handlers")

    def compile(self, raw: bytes, *, model_release_id: str) -> CompiledCatalog:
        bounds = JsonBounds(self._max_bytes, self._bounds.max_depth, self._max_handlers, self._max_bytes)
        value = parse_json(raw, bounds)
        if not isinstance(value, dict) or set(value) != {"tools"} or not isinstance(value.get("tools"), list):
            raise ToolsInvalid("CONFIG_INVALID")
        tools: dict[str, _ToolSchema] = {}
        for row in value["tools"]:
            if not isinstance(row, dict) or set(row) - {
                "name",
                "stage",
                "effect",
                "input_schema",
                "description",
                "controller_scope",
                "result_contract",
                "invariants",
            }:
                raise ToolsInvalid("CONFIG_INVALID")
            name, stage, effect, schema = (
                row.get("name"),
                row.get("stage"),
                row.get("effect"),
                row.get("input_schema"),
            )
            if not isinstance(name, str) or name not in tool_names() or name in tools:
                raise ToolsInvalid("CONFIG_INVALID")
            if (
                not isinstance(effect, str)
                or effect not in {"read", "compute", "proposal"}
                or not isinstance(stage, str)
            ):
                raise ToolsInvalid("CONFIG_INVALID")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ToolsInvalid("CONFIG_INVALID")
            _closed_schema(schema, self._max_bytes)
            try:
                phase = Phase(stage)
                Draft202012Validator.check_schema(schema)
            except (ValueError, SchemaError) as error:
                raise ToolsInvalid("CONFIG_INVALID") from error
            if effect == "read":
                kind: Literal["read", "compute", "proposal"] = "read"
            elif effect == "compute":
                kind = "compute"
            else:
                kind = "proposal"
            tools[name] = _ToolSchema(phase, kind, Draft202012Validator(schema))
        return CompiledCatalog(model_release_id, tools, self._bounds, self._result_bounds)
