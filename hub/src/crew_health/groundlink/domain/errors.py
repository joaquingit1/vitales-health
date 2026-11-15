"""CONTRACTS 12 transport validation failures."""


class GroundLinkError(Exception):
    pass


class DemoSwitchInvalid(GroundLinkError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


class BundleConflict(GroundLinkError):
    pass
