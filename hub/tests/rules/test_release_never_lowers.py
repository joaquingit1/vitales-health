"""A new configuration release never clears or lowers an active warning, and old warnings keep their pins (D1).

Property test over every sensor-limit definition of the deployment release (every level from watch to emergency,
every sensor type), every level a release could set for it, removal of the definition, an edited content block, a
changed environment list, and both activation kinds: the activation itself decides nothing, and the active warning
keeps its level, state, revision, definition pin, limit pin, fingerprint and release. A crossing evaluated under
the new release afterwards never lowers it either (DE24-7: a lower level waits until the pinned rule clears).
The CO2 sequences in `test_release_changes.py` (T-E24-013 to 017) show the same rule step by step.

T-E24-016, 017, 127; INV-E24-04, INV-E24-15; D1, H6, H11.
"""

from __future__ import annotations

import copy

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain.decisions import Clear
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.inputs import ActivationInput
from crew_health.rules.domain.vocabulary import ConditionKind, EnvironmentId, Level

pytestmark = [pytest.mark.E24, pytest.mark.trace("INV-E24-04", "INV-E24-15", "D1", "H6", "H11")]

RULES = parse_rules_document(h.deployment_rules())
SENSOR_DEFINITIONS = sorted(d.id for d in RULES.definitions if d.condition.kind is ConditionKind.SENSOR_LIMIT)
EDITS = st.sampled_from(["level", "remove", "content", "environments"])


def _edited(definition_id: str, edit: str, level: Level, environment: str) -> object:
    raw = copy.deepcopy(h.deployment_rules())
    definitions = raw["definitions"]
    assert isinstance(definitions, list)
    if edit == "remove":
        raw["definitions"] = [d for d in definitions if d["id"] != definition_id]
        return parse_rules_document(raw)
    (d,) = [d for d in definitions if d["id"] == definition_id]
    if edit == "level":
        d["level"] = level.value
    elif edit == "content":
        d["content"]["title"] += " (edited)"
    else:
        d["environments"] = [e for e in d["environments"] if e != environment] or ["transit"]
    return parse_rules_document(raw)


@pytest.mark.trace("T-E24-016", "T-E24-017", "T-E24-127")
@given(
    definition_id=st.sampled_from(SENSOR_DEFINITIONS),
    edit=EDITS,
    level=st.sampled_from(list(Level)),
    kind=st.sampled_from(["release", "environment"]),
)
@h.PROP
def test_no_release_edit_clears_lowers_or_repins_an_active_sensor_warning(
    definition_id: str, edit: str, level: Level, kind: str
) -> None:
    (definition,) = [d for d in RULES.definitions if d.id == definition_id]
    environment = sorted(e.value for e in definition.environments)[0]
    world = h.World(RULES, h.release_documents(), environment=EnvironmentId(environment))
    limit = world.limits[definition.condition.limit_id]  # type: ignore[attr-defined]
    if limit.status != "active" or limit.role != "warning":
        return  # a pending limit raises nothing (T-E24-027, 028)
    where = {"sensor_type": limit.sensor_type_id, "channel": "ch_" + limit.sensor_type_id}
    world.feed(world.sensor(results=[h.result(limit.limit_id, crossed=True, clear_holds=False)], **where))
    (w,) = world.by_definition(definition_id)
    before = dict(world.warnings)
    try:
        candidate = _edited(definition_id, edit, level, environment)
    except Exception:  # noqa: BLE001 - a candidate the parser refuses never activates (last good stays)
        return
    world.reload(candidate)  # type: ignore[arg-type]
    decision = world.feed(ActivationInput(kind, "rel_a", "rel_b", environment))  # type: ignore[arg-type]
    assert not any(isinstance(s, Clear) for s in decision)
    assert world.warnings == before  # nothing cleared, lowered or repinned by the activation
    world.advance(10)
    world.feed(world.sensor(release="rel_b", results=[h.result(limit.limit_id, crossed=True, clear_holds=False)],
                            reading="rdg_after", **where))  # fmt: skip
    kept = world.warnings[w.warning_id]
    if kept.active:
        assert kept == w  # still the same record: same level, pin, fingerprint, release, revision
    else:  # only a strictly higher level of the new release may supersede it (escalation, never lowering)
        (now,) = [x for x in world.warnings.values() if x.active and x.definition_id == definition_id]
        assert now.level > w.level and now.previous_warning_id == w.warning_id and kept.level is w.level
