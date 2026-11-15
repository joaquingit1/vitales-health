"""Typed, validated views of the three documents a help session pins (CONTRACTS 11 and 16).

Every step, question and flow word comes from responder-flows.json; every other word comes from
help-presentation.json or ui-copy.json. Parsing is strict: a document that does not match the shape the help
module reads is rejected as a whole, never patched with defaults.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from crew_health.help.domain.errors import HelpConfigInvalid

Level = Literal["act", "emergency"]
Basis = Literal["sourced", "team default"]
ReviewState = Literal["unreviewed", "reviewed", "rejected"]

# CONTRACTS 11 "Templates": the placeholders allowed in help-presentation templates. Contract vocabulary, not content.
HELP_PLACEHOLDERS = frozenset(
    {
        "subject_name",
        "opener_name",
        "helper_name",
        "helper_names",
        "place",
        "flow_title",
        "step",
        "note",
        "text",
        "disposition",
        "answers",
        "steps",
        "helping",
        "time",
        "delay",
        "in_time",
    }
)
LOG_TEMPLATE_KEYS = (
    "opened",
    "crew_called",
    "joined",
    "left",
    "step_done",
    "step_undone",
    "flow_switched",
    "started_over",
    "note",
    "assistant",
    "ground_message_queued",
    "ground_message_sent",
    "ground_message_acknowledged",
    "ground_reply",
    "closed",
)
DISPOSITIONS = (
    "resolved_on_board",
    "handed_to_crew_medical_officer",
    "ground_consulted",
    "false_alarm",
    "other",
)
BUTTON_IDS = ("call_crew", "tell_ground", "start_over")
ENVIRONMENT_KEYS = ("station_leo", "transit", "lunar_surface", "mars_surface", "eva")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TriageAnswer:
    label: str
    goes_to: str


@dataclass(frozen=True, slots=True)
class TriageNode:
    id: str
    question: str
    answers: tuple[TriageAnswer, ...]


@dataclass(frozen=True, slots=True)
class Measurement:
    what: str
    how: str
    instrument: str


@dataclass(frozen=True, slots=True)
class TreatmentStep:
    step: str
    who: str
    kit: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FlowDef:
    id: str
    title: str
    plain: str
    immediate_steps: tuple[str, ...]
    questions: tuple[str, ...]
    measurements: tuple[Measurement, ...]
    treatment_steps: tuple[TreatmentStep, ...]
    call_ground_when: str
    body_regions: tuple[str, ...]
    environment_notes: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]
    review_state: ReviewState

    def environment_note(self, environment_id: str | None) -> str | None:
        for key, text in self.environment_notes:
            if key == environment_id and text:
                return text
        return None


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class FlowDocument:
    triage: tuple[TriageNode, ...]
    flows: tuple[FlowDef, ...]
    sources: tuple[Source, ...]

    def triage_node(self, node_id: str) -> TriageNode | None:
        for node in self.triage:
            if node.id == node_id:
                return node
        return None

    def flow(self, flow_id: str) -> FlowDef | None:
        for flow in self.flows:
            if flow.id == flow_id:
                return flow
        return None

    def source_title(self, source_id: str) -> str | None:
        for source in self.sources:
            if source.id == source_id:
                return source.title
        return None


@dataclass(frozen=True, slots=True)
class FlowLevel:
    level: Level
    basis: Basis


@dataclass(frozen=True, slots=True)
class GroundTemplates:
    header: str
    answers: str
    steps: str
    crew: str
    queued: str
    waiting: str | None
    sent: str
    acknowledged: str


@dataclass(frozen=True, slots=True)
class Presentation:
    notice: str
    start_node_id: str
    fixed_buttons: tuple[tuple[str, str], ...]
    answer_log: tuple[tuple[str, int, str], ...]
    flow_levels: tuple[tuple[str, FlowLevel], ...]
    default_flow_level: Level
    broadcast_template: str
    helping_template: str
    nobody_helping: str
    readings_codes: tuple[str, ...]
    readings_max_age_minutes: int
    log_templates: tuple[tuple[str, str], ...]
    disposition_words: tuple[tuple[str, str], ...]
    ground: GroundTemplates
    assistant_waiting_text: str
    assistant_stopped_text: str
    red_flag_flows: tuple[tuple[str, str], ...]

    def log_template(self, key: str) -> str:
        for name, template in self.log_templates:
            if name == key:
                return template
        raise HelpConfigInvalid((f"help-presentation: log template {key} missing",))

    def answer_template(self, node_id: str, index: int) -> str | None:
        for node, answer_index, template in self.answer_log:
            if node == node_id and answer_index == index:
                return template
        return None

    def flow_level(self, flow_id: str) -> FlowLevel | None:
        for flow, level in self.flow_levels:
            if flow == flow_id:
                return level
        return None

    def disposition_word(self, disposition: str) -> str:
        for key, word in self.disposition_words:
            if key == disposition:
                return word
        raise HelpConfigInvalid((f"help-presentation: disposition word {disposition} missing",))

    def red_flag_flow_ids(self) -> frozenset[str]:
        return frozenset(flow for _, flow in self.red_flag_flows)


@dataclass(frozen=True, slots=True)
class UiWords:
    unknown: str
    body_region_labels: tuple[tuple[str, str], ...]
    duration_words: tuple[tuple[str, str], ...]
    step_done_line: str

    def body_region_label(self, region: str) -> str | None:
        for key, label in self.body_region_labels:
            if key == region:
                return label
        return None

    def duration_word(self, unit: str) -> str | None:
        for key, template in self.duration_words:
            if key == unit:
                return template
        return None


@dataclass(frozen=True, slots=True)
class ConfigPin:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class PinnedHelpDocs:
    release_id: str
    flows: FlowDocument
    presentation: Presentation
    ui: UiWords
    pins: tuple[ConfigPin, ...]


def normalize_text(value: str) -> str:
    """NFC, case fold, whitespace collapsed, trailing sentence marks stripped (E26 3.7)."""
    text = _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value).casefold()).strip()
    return text.rstrip(".!?").rstrip()


class _Reader:
    """Bounded, typed reads from parsed JSON with one finding per defect."""

    def __init__(self, document: str) -> None:
        self.document = document
        self.findings: list[str] = []

    def fail(self, pointer: str, message: str) -> None:
        if len(self.findings) < 200:
            self.findings.append(f"{self.document}{pointer}: {message}")

    def obj(self, value: object, pointer: str) -> Mapping[str, object]:
        if isinstance(value, dict) and all(isinstance(k, str) for k in value):
            return value
        self.fail(pointer, "expected an object")
        return {}

    def arr(self, value: object, pointer: str, *, min_items: int = 0) -> Sequence[object]:
        if isinstance(value, list):
            if len(value) < min_items:
                self.fail(pointer, f"expected at least {min_items} items")
            return value
        self.fail(pointer, "expected an array")
        return ()

    def text(self, value: object, pointer: str) -> str:
        if isinstance(value, str) and value:
            return value
        self.fail(pointer, "expected a non-empty string")
        return ""

    def opt_text(self, value: object, pointer: str) -> str | None:
        if value is None:
            return None
        return self.text(value, pointer)

    def integer(self, value: object, pointer: str, *, minimum: int) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
            return value
        self.fail(pointer, f"expected an integer >= {minimum}")
        return minimum

    def texts(self, value: object, pointer: str, *, min_items: int = 0) -> tuple[str, ...]:
        items = self.arr(value, pointer, min_items=min_items)
        return tuple(self.text(item, f"{pointer}/{i}") for i, item in enumerate(items))

    def template(self, value: object, pointer: str) -> str:
        text = self.text(value, pointer)
        stripped = _PLACEHOLDER.sub("", text)
        if "{" in stripped or "}" in stripped:
            self.fail(pointer, "unbalanced braces in template")
        for name in _PLACEHOLDER.findall(text):
            if name not in HELP_PLACEHOLDERS:
                self.fail(pointer, f"placeholder {{{name}}} is not allowed here (loader check 13)")
        return text


def _load(raw: bytes, name: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise HelpConfigInvalid((f"{name}: not valid UTF-8 JSON",)) from error


def parse_flows(raw: bytes) -> FlowDocument:
    reader = _Reader("responder-flows")
    root = reader.obj(_load(raw, "responder-flows"), "")
    triage = tuple(
        _triage_node(reader, node, f"/triage/{i}")
        for i, node in enumerate(reader.arr(root.get("triage"), "/triage", min_items=1))
    )
    flows = tuple(
        _flow(reader, flow, f"/flows/{i}")
        for i, flow in enumerate(reader.arr(root.get("flows"), "/flows", min_items=1))
    )
    sources = []
    for i, item in enumerate(reader.arr(root.get("sources"), "/sources", min_items=1)):
        source = reader.obj(item, f"/sources/{i}")
        sources.append(
            Source(reader.text(source.get("id"), f"/sources/{i}/id"), reader.text(source.get("title"), ""))
        )
    document = FlowDocument(triage, flows, tuple(sources))
    _check_flow_graph(reader, document)
    if reader.findings:
        raise HelpConfigInvalid(reader.findings)
    return document


def _triage_node(reader: _Reader, value: object, pointer: str) -> TriageNode:
    node = reader.obj(value, pointer)
    answers = []
    for i, item in enumerate(reader.arr(node.get("answers"), pointer + "/answers", min_items=1)):
        answer = reader.obj(item, f"{pointer}/answers/{i}")
        answers.append(
            TriageAnswer(
                reader.text(answer.get("answer"), f"{pointer}/answers/{i}/answer"),
                reader.text(answer.get("goes_to"), f"{pointer}/answers/{i}/goes_to"),
            )
        )
    return TriageNode(
        reader.text(node.get("id"), pointer + "/id"),
        reader.text(node.get("question"), pointer + "/question"),
        tuple(answers),
    )


def _flow(reader: _Reader, value: object, pointer: str) -> FlowDef:
    flow = reader.obj(value, pointer)
    measurements = []
    for i, item in enumerate(reader.arr(flow.get("measurements"), pointer + "/measurements")):
        m = reader.obj(item, f"{pointer}/measurements/{i}")
        p = f"{pointer}/measurements/{i}"
        measurements.append(
            Measurement(
                reader.text(m.get("what"), p + "/what"),
                reader.text(m.get("how"), p + "/how"),
                reader.text(m.get("instrument"), p + "/instrument"),
            )
        )
    treatments = []
    for i, item in enumerate(reader.arr(flow.get("treatment_steps"), pointer + "/treatment_steps")):
        t = reader.obj(item, f"{pointer}/treatment_steps/{i}")
        p = f"{pointer}/treatment_steps/{i}"
        treatments.append(
            TreatmentStep(
                reader.text(t.get("step"), p + "/step"),
                reader.text(t.get("who"), p + "/who"),
                reader.text(t.get("kit"), p + "/kit"),
                reader.texts(t.get("source_ids"), p + "/source_ids"),
            )
        )
    notes_obj = reader.obj(flow.get("environment_notes"), pointer + "/environment_notes")
    notes = tuple((key, text) for key in ENVIRONMENT_KEYS if isinstance(text := notes_obj.get(key), str))
    if len(notes) != len(ENVIRONMENT_KEYS):
        reader.fail(pointer + "/environment_notes", "every environment key needs a text")
    review = flow.get("review_state", "unreviewed")
    review_state: ReviewState = "unreviewed"
    if review in ("reviewed", "rejected"):
        review_state = "reviewed" if review == "reviewed" else "rejected"
    elif review != "unreviewed":
        reader.fail(pointer + "/review_state", "unknown review state")
    return FlowDef(
        id=reader.text(flow.get("id"), pointer + "/id"),
        title=reader.text(flow.get("title"), pointer + "/title"),
        plain=reader.text(flow.get("plain"), pointer + "/plain"),
        immediate_steps=reader.texts(flow.get("immediate_steps"), pointer + "/immediate_steps", min_items=1),
        questions=reader.texts(flow.get("questions"), pointer + "/questions"),
        measurements=tuple(measurements),
        treatment_steps=tuple(treatments),
        call_ground_when=reader.text(flow.get("call_ground_when"), pointer + "/call_ground_when"),
        body_regions=reader.texts(flow.get("body_regions"), pointer + "/body_regions"),
        environment_notes=notes,
        source_ids=reader.texts(flow.get("source_ids"), pointer + "/source_ids"),
        review_state=review_state,
    )


def _check_flow_graph(reader: _Reader, document: FlowDocument) -> None:
    triage_ids = [node.id for node in document.triage]
    flow_ids = [flow.id for flow in document.flows]
    if len(set(triage_ids)) != len(triage_ids) or len(set(flow_ids)) != len(flow_ids):
        reader.fail("", "node IDs must be unique")
    if set(triage_ids) & set(flow_ids):
        reader.fail("", "a triage node and a flow share an ID")
    targets = set(triage_ids) | set(flow_ids)
    for i, node in enumerate(document.triage):
        for j, answer in enumerate(node.answers):
            if answer.goes_to not in targets:
                reader.fail(f"/triage/{i}/answers/{j}/goes_to", "goes to no triage node or flow (check 3)")
    for i, flow in enumerate(document.flows):
        texts = [*flow.immediate_steps, *flow.questions, *(m.what for m in flow.measurements)]
        normalized = [normalize_text(t) for t in texts]
        if len(set(normalized)) != len(normalized):
            reader.fail(f"/flows/{i}", "normalized step, question and measurement texts must be unique")


def parse_presentation(raw: bytes) -> Presentation:
    r = _Reader("help-presentation")
    root = r.obj(_load(raw, "help-presentation"), "")
    buttons = []
    for i, item in enumerate(r.arr(root.get("fixed_buttons"), "/fixed_buttons", min_items=3)):
        b = r.obj(item, f"/fixed_buttons/{i}")
        buttons.append((r.text(b.get("id"), f"/fixed_buttons/{i}/id"), r.text(b.get("label"), "")))
    if sorted(b[0] for b in buttons) != sorted(BUTTON_IDS):
        r.fail("/fixed_buttons", "exactly call_crew, tell_ground and start_over")
    answer_log = []
    for i, item in enumerate(r.arr(root.get("answer_log"), "/answer_log")):
        a = r.obj(item, f"/answer_log/{i}")
        answer_log.append(
            (
                r.text(a.get("node_id"), f"/answer_log/{i}/node_id"),
                r.integer(a.get("answer_index"), f"/answer_log/{i}/answer_index", minimum=0),
                r.template(a.get("log_template"), f"/answer_log/{i}/log_template"),
            )
        )
    levels = []
    for i, item in enumerate(r.arr(root.get("flow_levels"), "/flow_levels", min_items=1)):
        f = r.obj(item, f"/flow_levels/{i}")
        levels.append(
            (
                r.text(f.get("flow_id"), f"/flow_levels/{i}/flow_id"),
                FlowLevel(_level(r, f.get("level"), f"/flow_levels/{i}/level"), _basis(r, f.get("basis"))),
            )
        )
    broadcast = r.obj(root.get("broadcast"), "/broadcast")
    readings = r.obj(root.get("readings_panel"), "/readings_panel")
    logs = r.obj(root.get("log_templates"), "/log_templates")
    words = r.obj(root.get("disposition_words"), "/disposition_words")
    assistant = r.obj(root.get("assistant_line"), "/assistant_line")
    red_flags = []
    for i, item in enumerate(r.arr(root.get("red_flag_flows"), "/red_flag_flows", min_items=1)):
        rf = r.obj(item, f"/red_flag_flows/{i}")
        red_flags.append((r.text(rf.get("question_id"), ""), r.text(rf.get("flow_id"), "")))
    presentation = Presentation(
        notice=r.text(root.get("notice"), "/notice"),
        start_node_id=r.text(root.get("start_node_id"), "/start_node_id"),
        fixed_buttons=tuple(buttons),
        answer_log=tuple(answer_log),
        flow_levels=tuple(levels),
        default_flow_level=_level(r, root.get("default_flow_level"), "/default_flow_level"),
        broadcast_template=r.template(broadcast.get("template"), "/broadcast/template"),
        helping_template=r.template(broadcast.get("helping_template"), "/broadcast/helping_template"),
        nobody_helping=r.text(broadcast.get("nobody_helping"), "/broadcast/nobody_helping"),
        readings_codes=r.texts(readings.get("codes"), "/readings_panel/codes", min_items=1),
        readings_max_age_minutes=r.integer(readings.get("max_age_minutes"), "/readings_panel", minimum=1),
        log_templates=tuple((k, r.template(logs.get(k), f"/log_templates/{k}")) for k in LOG_TEMPLATE_KEYS),
        disposition_words=tuple((k, r.text(words.get(k), f"/disposition_words/{k}")) for k in DISPOSITIONS),
        ground=_ground(r, r.obj(root.get("ground_message"), "/ground_message")),
        assistant_waiting_text=r.text(assistant.get("waiting_text"), "/assistant_line/waiting_text"),
        assistant_stopped_text=r.text(assistant.get("stopped_text"), "/assistant_line/stopped_text"),
        red_flag_flows=tuple(red_flags),
    )
    if r.findings:
        raise HelpConfigInvalid(r.findings)
    return presentation


def _ground(r: _Reader, ground: Mapping[str, object]) -> GroundTemplates:
    waiting = ground.get("waiting_template")
    return GroundTemplates(
        header=r.template(ground.get("header_template"), "/ground_message/header_template"),
        answers=r.template(ground.get("answers_template"), "/ground_message/answers_template"),
        steps=r.template(ground.get("steps_template"), "/ground_message/steps_template"),
        crew=r.template(ground.get("crew_template"), "/ground_message/crew_template"),
        queued=r.template(ground.get("queued_template"), "/ground_message/queued_template"),
        waiting=None if waiting is None else r.template(waiting, "/ground_message/waiting_template"),
        sent=r.template(ground.get("sent_template"), "/ground_message/sent_template"),
        acknowledged=r.template(ground.get("acknowledged_template"), "/ground_message/acknowledged_template"),
    )


def _level(r: _Reader, value: object, pointer: str) -> Level:
    if value == "act":
        return "act"
    if value == "emergency":
        return "emergency"
    r.fail(pointer, "level must be act or emergency")
    return "emergency"


def _basis(r: _Reader, value: object) -> Basis:
    if value == "sourced":
        return "sourced"
    if value == "team default":
        return "team default"
    r.fail("/basis", "basis must be sourced or team default")
    return "team default"


def parse_ui_words(raw: bytes) -> UiWords:
    r = _Reader("ui-copy")
    root = r.obj(_load(raw, "ui-copy"), "")
    states = r.obj(root.get("designed_states"), "/designed_states")
    labels = r.obj(root.get("body_region_labels"), "/body_region_labels")
    durations = r.obj(root.get("duration_words"), "/duration_words")
    catch_up = r.obj(root.get("catch_up_words"), "/catch_up_words")
    words = UiWords(
        unknown=r.text(states.get("unknown"), "/designed_states/unknown"),
        body_region_labels=tuple(
            (key, r.text(value, f"/body_region_labels/{key}")) for key, value in sorted(labels.items())
        ),
        duration_words=tuple(
            (key, r.text(value, f"/duration_words/{key}")) for key, value in sorted(durations.items())
        ),
        step_done_line=r.text(catch_up.get("step_done_line"), "/catch_up_words/step_done_line"),
    )
    if r.findings:
        raise HelpConfigInvalid(r.findings)
    return words


def check_presentation_against_flows(presentation: Presentation, flows: FlowDocument) -> tuple[str, ...]:
    """Loader check 10 plus the help checks of E26 section 8."""
    findings: list[str] = []
    if flows.triage_node(presentation.start_node_id) is None:
        findings.append("help-presentation/start_node_id: not a triage node")
    for flow_id, _ in presentation.flow_levels:
        if flows.flow(flow_id) is None:
            findings.append(f"help-presentation/flow_levels: unknown flow {flow_id}")
    for question_id, flow_id in presentation.red_flag_flows:
        if flows.flow(flow_id) is None:
            findings.append(f"help-presentation/red_flag_flows: {question_id} names unknown flow {flow_id}")
    for node_id, index, _ in presentation.answer_log:
        node = flows.triage_node(node_id)
        if node is None or index >= len(node.answers):
            findings.append(f"help-presentation/answer_log: unknown answer {node_id}[{index}]")
    return tuple(findings)


def pin_documents(
    release_id: str,
    *,
    flows_raw: bytes,
    presentation_raw: bytes,
    ui_raw: bytes,
    pins: Sequence[ConfigPin],
) -> PinnedHelpDocs:
    flows = parse_flows(flows_raw)
    presentation = parse_presentation(presentation_raw)
    ui = parse_ui_words(ui_raw)
    findings = check_presentation_against_flows(presentation, flows)
    if findings:
        raise HelpConfigInvalid(findings)
    return PinnedHelpDocs(release_id, flows, presentation, ui, tuple(pins))
