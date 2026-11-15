"""Hub settings of the warnings engine (E24 5.4, SYSTEM 5.11). Operational bounds only: no safety check, clear
condition or level can be changed through any of them."""

from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import SettingSpec

BASIS = "docs/tdd/E24.md section 5.4 operational bounds, basis team default"

# key, seed (also the ceiling), minimum
INTEGERS: tuple[tuple[str, int, int], ...] = (
    ("rules.max_definitions", 500, 1),
    ("rules.page_limit", 100, 1),
    ("rules.max_notes_per_warning", 100, 1),
    ("rules.max_note_chars", 2000, 1),
    ("rules.max_banner_items", 5, 1),
    ("rules.pending_max_rows", 10000, 1),
    ("rules.reeval_batch", 100, 1),
    ("rules.reeval_max_attempts", 10, 1),
    ("rules.reeval_backoff_initial_s", 1, 1),
    ("rules.reeval_backoff_max_s", 3600, 1),
    ("rules.eval_budget_ms", 50, 1),
    ("rules.active_scan_max", 10000, 1),
    ("rules.occurrence_scan_max", 1000, 1),
    ("rules.answer_event_window", 50, 1),
    ("rules.clear_scan_max", 10000, 1),
    ("rules.dwell_scan_max", 10000, 1),
    ("rules.action_replay_hours", 24, 1),
)
TEXTS: tuple[tuple[str, int], ...] = (("rules.node_id", 128),)

DEFAULTS: dict[str, object] = {
    **{key: seed for key, seed, _ in INTEGERS},
    "rules.node_id": "",
}


def specifications() -> tuple[SettingSpec, ...]:
    ints = tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(seed), None, BASIS)
        for key, seed, low in INTEGERS
    )
    texts = tuple(SettingSpec(key, "text", None, None, None, size, BASIS) for key, size in TEXTS)
    return (*ints, *texts)
