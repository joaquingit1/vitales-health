from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import CommandIdentity, PendingCommand, StoredResult
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.domain.commands import identity_bytes
from crew_health.platform.domain.errors import fail


class IntentRepository:
    def __init__(self, ledger: CommandLedger) -> None:
        self.ledger = ledger

    def reserve(
        self, uow: UnitOfWork, identity: CommandIdentity, *, resume_handler: str, input_ref: str
    ) -> PendingCommand:
        existing = self.pending(uow, identity)
        if existing is not None:
            if (existing.resume_handler, existing.input_ref) != (resume_handler, input_ref):
                raise fail(ErrorCode.DUPLICATE_COMMAND)
            return existing
        if self.ledger.lookup(uow, identity) is not None:
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        intent = PendingCommand(self.ledger.ids.new("intent"), identity, resume_handler, input_ref)
        uow.connection.execute(
            "INSERT INTO platform_command_intent VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                identity.actor_ref,
                identity.key,
                identity_bytes(identity),
                intent.intent_id,
                resume_handler,
                input_ref,
            ),
        )
        return intent

    def pending(self, view: ReadSnapshot | UnitOfWork, identity: CommandIdentity) -> PendingCommand | None:
        rows = view.connection.rows(
            "SELECT identity, intent_id, resume_handler, input_ref, completed FROM platform_command_intent WHERE actor_ref=? AND command_key=?",
            (identity.actor_ref, identity.key),
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        if row[0] != identity_bytes(identity):
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        if row[4]:
            return None
        return PendingCommand(str(row[1]), identity, str(row[2]), str(row[3]))

    def complete(self, uow: UnitOfWork, intent: PendingCommand, result: StoredResult) -> None:
        current = self.pending(uow, intent.identity)
        if current != intent:
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        self.ledger.record(uow, intent.identity, result)
        uow.connection.execute(
            "UPDATE platform_command_intent SET completed=1 WHERE intent_id=?", (intent.intent_id,)
        )
