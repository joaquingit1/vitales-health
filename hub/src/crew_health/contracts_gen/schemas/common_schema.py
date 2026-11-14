# Generated from common.schema.json; preserves #/$defs/instant/pattern.
from re import search as _schema_pattern_search
from pydantic import field_validator as _schema_field_validator
from typing import Any, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel

class CommonDefinitions(RootModel[Any]):
    root: Any = Field(..., description='Shared definitions referenced by every other schema. It validates nothing on its own.', title='Common definitions')

class Id(RootModel[str]):
    root: str = Field(..., description='An opaque object ID issued by the hub. Never a display name.', pattern='^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')

class ConfigId(RootModel[str]):
    root: str = Field(..., description='A stable snake_case ID defined in a configuration document (hazard, item, flow, question, definition).', pattern='^[a-z][a-z0-9_]{0,63}$')

class DottedId(RootModel[str]):
    root: str = Field(..., description='A stable ID made of configuration IDs joined by dots, for example daily_check.co2_above_limit.', pattern='^[a-z][a-z0-9_]{0,63}(\\.[a-z0-9_]{1,64}){0,3}$')

class Instant(RootModel[AwareDatetime]):
    root: AwareDatetime = Field(..., description='UTC instant, RFC 3339 with Z.')

    @_schema_field_validator('root', mode='before')
    @classmethod
    def _validate_wire_pattern(cls, value: object) -> object:
        if not isinstance(value, str) or _schema_pattern_search('Z$', value) is None:
            raise ValueError('wire instant pattern mismatch')
        return value

class Offset1(RootModel[str]):
    root: str = Field(..., description='Original UTC offset where known, for example +00:00.', pattern='^[+-]\\d{2}:\\d{2}$')

class Offset(RootModel[Offset1 | None]):
    root: Offset1 | None

class LocalTime(RootModel[str]):
    root: str = Field(..., description='Mission time of day, HH:MM, on the mission clock.', pattern='^([01]\\d|2[0-3]):[0-5]\\d$')

class MissionDay(RootModel[int]):
    root: int = Field(..., description='Mission day number on the mission clock. Day 1 starts at the mission epoch. Pre-flight days are zero or negative.')

class Met(RootModel[str]):
    root: str = Field(..., description='Mission elapsed time DDD/HH:MM:SS.', pattern='^\\d{3}/\\d{2}:\\d{2}:\\d{2}$')

class EnvironmentId(RootModel[Literal['station_leo', 'transit', 'lunar_surface', 'mars_surface']]):
    root: Literal['station_leo', 'transit', 'lunar_surface', 'mars_surface'] = Field(..., description='One of the four mission environments.')

class HazardEnvironmentKey(RootModel[Literal['station_leo', 'transit', 'lunar_surface', 'mars_surface', 'eva']]):
    root: Literal['station_leo', 'transit', 'lunar_surface', 'mars_surface', 'eva'] = Field(..., description='Environment keys used inside environment-hazards.json and responder-flows.json, which add eva for notes about being outside.')

class Overlay(RootModel[Literal['eva', 'post_eva', 'exercise', 'sleep']]):
    root: Literal['eva', 'post_eva', 'exercise', 'sleep'] = Field(..., description='An activity overlay on top of the environment.')

class Level(RootModel[Literal['info', 'watch', 'act', 'emergency']]):
    root: Literal['info', 'watch', 'act', 'emergency'] = Field(..., description='Warning level. Display words come from ui-copy.')

class RaisedBy(RootModel[Literal['rule', 'sensor_limit', 'assistant']]):
    root: Literal['rule', 'sensor_limit', 'assistant'] = Field(..., description="What raised a warning. A person's emergency is a help session, not a warning.")

class CadenceWord(RootModel[Literal['continuous', 'daily', 'every_n_days', 'weekly', 'monthly', 'per_eva', 'after_event', 'mission_phase', 'as_needed']]):
    root: Literal['continuous', 'daily', 'every_n_days', 'weekly', 'monthly', 'per_eva', 'after_event', 'mission_phase', 'as_needed']

class N(RootModel[int]):
    root: int = Field(..., ge=1)

class Cadence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    word: CadenceWord
    n: N | None
    event: str | None = Field(None, description='The event in words, for after_event.', min_length=1)
    phase: str | None = Field(None, description='The mission phase in words, for mission_phase.', min_length=1)
    note: str | None = Field(None, min_length=1)

class BodyRegion(RootModel[Literal['head', 'eyes', 'ear', 'neck', 'shoulder', 'heart', 'lungs', 'abdomen', 'flank', 'back', 'wrist', 'hand', 'legs', 'knee', 'ankle']]):
    root: Literal['head', 'eyes', 'ear', 'neck', 'shoulder', 'heart', 'lungs', 'abdomen', 'flank', 'back', 'wrist', 'hand', 'legs', 'knee', 'ankle'] = Field(..., description='Fixed body region set body-regions-1 (CONTRACTS section 7).')

class BodyRegionOrNull(RootModel[BodyRegion | None]):
    root: BodyRegion | None = Field(..., description='null means not about one place.')

class BodyRegions(RootModel[list[BodyRegion]]):
    root: list[BodyRegion] = Field(..., description='Body regions; an empty list means not about one place.')

class Laterality(RootModel[Literal['left', 'right', 'both', 'unknown']]):
    root: Literal['left', 'right', 'both', 'unknown']

class OriginMode(RootModel[Literal['LIVE', 'REPLAY', 'SIM']]):
    root: Literal['LIVE', 'REPLAY', 'SIM'] = Field(..., description='LIVE, REPLAY or SIM (CONTRACTS section 1).')

class AcquisitionMethod(RootModel[Literal['self_report', 'manual_instrument', 'device', 'camera_estimate', 'crewmate_palpation', 'imported_report', 'clinician_entry', 'derived']]):
    root: Literal['self_report', 'manual_instrument', 'device', 'camera_estimate', 'crewmate_palpation', 'imported_report', 'clinician_entry', 'derived'] = Field(..., description='crewmate_palpation: a crewmate felt and counted it in a guided check (CONTRACTS section 23); operator_id is the crewmate.')

class QualityStatus(RootModel[Literal['accepted', 'rejected', 'uncertain', 'not_assessed']]):
    root: Literal['accepted', 'rejected', 'uncertain', 'not_assessed']

class Presence(RootModel[Literal['present', 'absent', 'unknown', 'declined']]):
    root: Literal['present', 'absent', 'unknown', 'declined'] = Field(..., description='Answer presence state.')

class SourceId(RootModel[str]):
    root: str = Field(..., description="A source ID from a configuration document's sources list or the package source registry.", pattern='^(EV|RT|RS|SK|S|C|N|L)\\d{2}$')

class SourceIds(RootModel[list[SourceId]]):
    root: list[SourceId]

class Basis(RootModel[Literal['sourced', 'team default']]):
    root: Literal['sourced', 'team default'] = Field(..., description='sourced: the value is in a cited source. team default: chosen by the team, to be set by clinical review or the flight surgeon.')

class ToBeSetBy(RootModel[Literal['clinical review', 'the flight surgeon', 'flight rules'] | None]):
    root: Literal['clinical review', 'the flight surgeon', 'flight rules'] | None

class GapKind(RootModel[Literal['planned_line_of_sight', 'relay_outage', 'conjunction', 'equipment', 'unknown']]):
    root: Literal['planned_line_of_sight', 'relay_outage', 'conjunction', 'equipment', 'unknown'] = Field(..., description='Why the ground link is down or delayed.')

class PlanMode(RootModel[Literal['resolve', 'talk_through', 'stabilize', 'prepare_return']]):
    root: Literal['resolve', 'talk_through', 'stabilize', 'prepare_return'] = Field(..., description='How a plan handles the problem: resolve here, talk a crewmate through it, stabilize and watch, or prepare to return (only where the environment has a return option).')

class PlanStage(RootModel[Literal['check_another_way', 'do_now', 'watch', 'if_worse', 'medical_officer', 'return', 'ground_log']]):
    root: Literal['check_another_way', 'do_now', 'watch', 'if_worse', 'medical_officer', 'return', 'ground_log'] = Field(..., description='Plan stages, always in this order; ground_log is required and last.')

class Who(RootModel[str]):
    root: str = Field(..., description='Who may do a plan step: any_crew, crew_medical_officer, or skill:<skill_id> for a crewmate holding that skill from skills-catalog.json.', pattern='^(any_crew|crew_medical_officer|skill:[a-z][a-z0-9_]{0,63})$')

class PctRange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    min_pct: float = Field(..., ge=0.0, le=100.0)
    max_pct: float = Field(..., ge=0.0, le=100.0)

class Rtdc(BaseModel):
    model_config = ConfigDict(extra='forbid')
    treated_best: PctRange
    treated_worst: PctRange
    untreated_best: PctRange
    untreated_worst: PctRange
    source_id: SourceId
    page: int = Field(..., description='Page of the saved form that holds Table 4.', ge=1)

class WhatHappenedItem(RootModel[str]):
    root: str = Field(..., min_length=1)

class WhatWasDoneItem(RootModel[str]):
    root: str = Field(..., min_length=1)

class BeingWatchedItem(RootModel[str]):
    root: str = Field(..., min_length=1)

class AdviceWelcomeItem(RootModel[str]):
    root: str = Field(..., min_length=1)

class CatchUp(BaseModel):
    model_config = ConfigDict(extra='forbid')
    what_happened: list[WhatHappenedItem] = Field(..., min_length=1)
    what_was_done: list[WhatWasDoneItem]
    being_watched: list[BeingWatchedItem]
    advice_welcome: list[AdviceWelcomeItem]

class GroundNeeds(BaseModel):
    model_config = ConfigDict(extra='forbid')
    diagnosis: bool
    treatment: bool
    follow_up: bool
    quote: str = Field(..., description="The form's Communications and Support Needs words.", min_length=1)
    source_id: SourceId
    page: int = Field(..., ge=1)

class FormQuote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote: str = Field(..., description='Words copied from the saved form.', min_length=1)
    source_id: SourceId
    page: int = Field(..., ge=1)

class ConfigName(RootModel[Literal['environment-hazards', 'routine-schedule', 'responder-flows', 'sensor-catalog', 'sensor-limits', 'warning-rules', 'routine-rules', 'environment-profiles', 'questionnaires', 'indicator-catalog', 'help-presentation', 'assistant-policy', 'ui-copy', 'participation-rules', 'kit-map', 'onboard-plans', 'skills-catalog', 'role-rules']]):
    root: Literal['environment-hazards', 'routine-schedule', 'responder-flows', 'sensor-catalog', 'sensor-limits', 'warning-rules', 'routine-rules', 'environment-profiles', 'questionnaires', 'indicator-catalog', 'help-presentation', 'assistant-policy', 'ui-copy', 'participation-rules', 'kit-map', 'onboard-plans', 'skills-catalog', 'role-rules'] = Field(..., description='Configuration document name; the file is <name>.json.')

class Sha256(RootModel[str]):
    root: str = Field(..., pattern='^[a-f0-9]{64}$')

class ConfigPin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: ConfigName
    version: Sha256

class Role(RootModel[Literal['crew', 'crew_medical_officer', 'ground', 'operator', 'station', 'adapter', 'editor']]):
    root: Literal['crew', 'crew_medical_officer', 'ground', 'operator', 'station', 'adapter', 'editor'] = Field(..., description='Session roles. station is a shared habitat screen with no person signed in.')

class DisplayName(RootModel[str]):
    root: str = Field(..., min_length=1)

class Actor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['person', 'station', 'ground', 'operator', 'adapter', 'engine', 'assistant']
    id: Id
    display_name: DisplayName | None = None

class PersonRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    person_id: Id
    display_name: str = Field(..., min_length=1)

class Text(RootModel[str]):
    root: str = Field(..., min_length=1)

class FreeText(RootModel[str]):
    root: str = Field(..., description='Free text written by a person.', min_length=1)

class Trace(RootModel[str]):
    root: str = Field(..., description='The full provenance sentence, for example the dataset, date and any conversion.', min_length=1)

class Dataset(RootModel[str]):
    root: str = Field(..., min_length=1)

class Provenance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin_mode: OriginMode
    source_label: str = Field(..., description='Short words shown next to a value, for example Replay, ISS 2014 or Sim.', min_length=1)
    trace: Trace | None = None
    dataset: Dataset | None = None
    source_ids: SourceIds | None = None

class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['reading', 'observation', 'answer', 'daily_check', 'mission_event', 'state_fact', 'insight', 'warning', 'routine_occurrence', 'help_session', 'plan_run', 'guided_check', 'supply_use']
    ref_id: Id
    summary: str | None = Field(None, description='Plain words the client may show.', min_length=1)
    observed_at: Instant | None = None

class Citation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(..., description='The citation line, rendered by the hub from source titles.', min_length=1)
    source_ids: SourceIds
    basis: Basis | None = None

class Page(BaseModel):
    model_config = ConfigDict(extra='forbid')
    next_cursor: str | None
    total: int | None = Field(None, ge=0)

class AllowedAction(RootModel[str]):
    root: str = Field(..., pattern='^[a-z_]+$')

class AllowedActions(RootModel[list[AllowedAction]]):
    root: list[AllowedAction] = Field(..., description='Actions this session may take on the object now. Computed by the hub; the client never infers them.')

class EnvironmentDay(RootModel[int]):
    root: int = Field(..., description="Day count since the active environment profile became active, using that profile's day length.", ge=1)

class DayRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mission_day: MissionDay
    environment_day: EnvironmentDay | None = None
    label: str = Field(..., description="Rendered from the environment profile's day template, for example day 21 or sol 7.", min_length=1)

class NumberDisplay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: float | None
    unit: str
    text: str = Field(..., description='The rendered value with the decimals from the catalog, or the no-reading word.', min_length=1)
