from decimal import Decimal

from crew_health.kernel.settings import SettingSpec


def specifications() -> tuple[SettingSpec, ...]:
    return (
        SettingSpec(
            "text_rules.max_text_bytes",
            "integer",
            None,
            Decimal(1),
            Decimal(8388608),
            None,
            "E01 design section 11 bounded authored text scan",
        ),
        SettingSpec(
            "text_rules.max_matches",
            "integer",
            None,
            Decimal(1),
            Decimal(10000),
            None,
            "E01 design section 11 bounded authored text matches",
        ),
        SettingSpec(
            "config.ui_copy_cache_max",
            "integer",
            None,
            Decimal(1),
            Decimal(64),
            None,
            "SYSTEM 5.6 parsed ui-copy unknown text, one entry per ui-copy version",
        ),
    )
