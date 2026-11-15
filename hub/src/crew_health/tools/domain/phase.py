from enum import StrEnum


class Phase(StrEnum):
    INTAKE = "intake"
    ASSESSMENT = "assessment"
    ACQUISITION = "acquisition"
    DISPOSITION = "disposition"
    HELP = "help"
    INSIGHT = "insight"
    GENERAL = "general"
    VERIFIER = "verifier"
