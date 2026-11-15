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
    )
