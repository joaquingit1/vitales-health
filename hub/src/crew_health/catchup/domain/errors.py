"""Typed catch-up failures, with safe wire mapping in the API."""


class CatchUpError(Exception):
    pass


class CatchUpUnavailable(CatchUpError):
    pass


class LogTooLarge(CatchUpError):
    pass


class CompositionTooLarge(CatchUpError):
    pass


class ConsentRequired(CatchUpError):
    pass
