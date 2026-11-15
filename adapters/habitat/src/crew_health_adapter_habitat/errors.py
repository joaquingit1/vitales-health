"""Typed errors of the habitat adapter. Every message is a safe code; none carries reading text or a token."""

from __future__ import annotations


class AdapterError(Exception):
    """Base of every adapter failure. `code` is a stable, safe, allowlisted identifier."""

    code = "ADAPTER_ERROR"

    def __init__(self, detail: str = "") -> None:
        super().__init__(self.code + (": " + detail if detail else ""))
        self.detail = detail


class SettingsInvalid(AdapterError):
    code = "SETTINGS_INVALID"


class FeedInvalid(AdapterError):
    """A feed document or entry is refused. Nothing is partly accepted."""

    code = "FEED_INVALID"


class ProfileRefused(AdapterError):
    """Demo replay and sim refuse to start unless the deployment profile is demo (E23 5.2)."""

    code = "PROFILE_REFUSED"


class LiveRefused(AdapterError):
    """LIVE input is refused while its sensor type has no sourced plausibility criteria (R-E23-2)."""

    code = "LIVE_REFUSED"


class ParseRejected(AdapterError):
    """One input row, characteristic or packet cannot be parsed. It is counted; no value is substituted."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail)


class SourceUnavailable(AdapterError):
    """The input is absent: a missing file, a lost link, no device. Absence is shown, never filled."""

    code = "SOURCE_UNAVAILABLE"


class QueueFull(AdapterError):
    """The bounded queue holds `habitat_adapter.queue_max` rows. The source pauses; nothing is dropped."""

    code = "QUEUE_FULL"


class QueueStorageError(AdapterError):
    """Disk full, corrupt or unreadable queue file. The command fails loudly; source progress is unchanged."""

    code = "QUEUE_STORAGE"


class TransportFault(AdapterError):
    """The hub answered in a way that must keep the batch queued under its original key and body."""

    code = "TRANSPORT_FAULT"


class CommandConflict(TransportFault):
    """A 409 that is not the closed accepted_command receipt of this batch. Quarantined, never re-keyed."""

    code = "COMMAND_CONFLICT"


class ResultMismatch(TransportFault):
    """The per-index result does not reconcile with the batch. The batch stays queued."""

    code = "RESULT_MISMATCH"


class RetryExhausted(TransportFault):
    code = "RETRY_EXHAUSTED"
