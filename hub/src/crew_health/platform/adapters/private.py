from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.private import PrivateReceipt, SealedPrivateRef
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.platform.domain.errors import fail


class PrivateReferences:
    def get(
        self, view: ReadSnapshot | UnitOfWork, entry_id: str
    ) -> tuple[SealedPrivateRef, PrivateReceipt, bool] | None:
        rows = view.connection.rows(
            "SELECT seq, entry_id, version_id, record_kind, owner_person_id, command_key, fingerprint, key_id, ciphertext_sha256, deleted FROM platform_private_reference WHERE entry_id=?",
            (entry_id,),
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        reference = SealedPrivateRef(*(str(v) for v in row[1:9]))
        return (
            reference,
            PrivateReceipt(reference.entry_id, reference.command_key, int(str(row[0]))),
            bool(row[9]),
        )

    def attach(self, uow: UnitOfWork, reference: SealedPrivateRef) -> PrivateReceipt:
        prior = self.get(uow, reference.entry_id)
        if prior is not None:
            if prior[0] != reference or prior[2]:
                raise fail(ErrorCode.DUPLICATE_COMMAND)
            return prior[1]
        result = uow.connection.execute(
            "INSERT INTO platform_private_reference(entry_id,version_id,record_kind,owner_person_id,command_key,fingerprint,key_id,ciphertext_sha256) VALUES (?,?,?,?,?,?,?,?)",
            (
                reference.entry_id,
                reference.version_id,
                reference.record_kind,
                reference.owner_person_id,
                reference.command_key,
                reference.fingerprint,
                reference.key_id,
                reference.ciphertext_sha256,
            ),
        )
        if result.last_row_id is None:
            raise fail(ErrorCode.INTERNAL_ERROR)
        return PrivateReceipt(reference.entry_id, reference.command_key, result.last_row_id)
