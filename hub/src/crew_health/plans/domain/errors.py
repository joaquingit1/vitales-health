"""Typed failures mapped to transport errors by the application boundary."""


class PlansError(Exception):
    """Base for plan-domain failures."""


class InvalidField(PlansError):
    def __init__(self, pointer: str, reason: str) -> None:
        self.pointer = pointer
        self.reason = reason
        super().__init__(pointer, reason)


class StepNotAvailable(PlansError):
    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        super().__init__(step_id)


class InvalidTransition(PlansError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class StaleVersion(PlansError):
    """A noncommuting command used an older revision."""


class PlansDocumentInvalid(PlansError):
    def __init__(self, pointer: str, reason: str) -> None:
        self.pointer = pointer
        self.reason = reason
        super().__init__(pointer, reason)


class UnitMismatch(PlansError):
    def __init__(self, supply_id: str) -> None:
        self.supply_id = supply_id
        super().__init__(supply_id)
