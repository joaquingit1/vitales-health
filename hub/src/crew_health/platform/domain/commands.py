from crew_health.kernel.commands import CommandIdentity, canonical_json


def identity_bytes(identity: CommandIdentity) -> bytes:
    return canonical_json(
        {
            "key": identity.key,
            "actor_ref": identity.actor_ref,
            "operation": identity.operation,
            "target": identity.target,
            "parameters_sha256": identity.parameters_sha256,
            "body_fingerprint": identity.body_fingerprint,
            "fingerprint_kind": identity.fingerprint_kind,
        }
    )
