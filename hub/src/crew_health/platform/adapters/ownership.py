from typing import Literal, cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.ownership import OwnedResource, Transfer, TransferReceipt
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.platform.adapters import codec
from crew_health.platform.domain.errors import fail


class OwnershipRepository:
    def __init__(self) -> None:
        self.head_codec = codec(OwnedResource)
        self.transfer_codec = codec(Transfer)

    def head(
        self, view: ReadSnapshot | UnitOfWork, kind: str, resource_id: str
    ) -> tuple[OwnedResource, bool] | None:
        rows = view.connection.rows(
            "SELECT head,fenced FROM platform_ownership WHERE kind=? AND resource_id=?",
            (kind, resource_id),
            limit=1,
        )
        if not rows:
            return None
        if not isinstance(rows[0][0], bytes):
            raise fail(ErrorCode.INTERNAL_ERROR)
        return self.head_codec.validate_json(rows[0][0]), bool(rows[0][1])

    def transfer(
        self, view: ReadSnapshot | UnitOfWork, transfer_id: str
    ) -> tuple[Transfer, TransferReceipt] | None:
        rows = view.connection.rows(
            "SELECT transfer,phase,certificate FROM platform_transfer WHERE transfer_id=?",
            (transfer_id,),
            limit=1,
        )
        if not rows:
            return None
        raw, phase, certificate = rows[0]
        if (
            not isinstance(raw, bytes)
            or phase not in ("prepared", "fenced", "accepted", "completed", "cancelled")
            or not isinstance(certificate, bytes)
        ):
            raise fail(ErrorCode.INTERNAL_ERROR)
        return self.transfer_codec.validate_json(raw), TransferReceipt(
            transfer_id,
            cast(Literal["prepared", "fenced", "accepted", "completed", "cancelled"], phase),
            certificate,
        )
