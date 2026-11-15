from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash


@dataclass(frozen=True, slots=True)
class LicenseRecord:
    license_id: str
    content_sha256: str
    kind: Literal["nasa_public_domain", "permission_documented", "metadata_only"]
    permission_basis: str | None
    recorded_by: str
    at: datetime

    def __post_init__(self) -> None:
        require_id(self.license_id)
        require_hash(self.content_sha256)
        require_utc(self.at)
        if self.kind not in ("nasa_public_domain", "permission_documented", "metadata_only"):
            raise EvidenceInvalid("license_kind")
        if self.kind != "metadata_only" and (not self.permission_basis or not self.recorded_by):
            raise EvidenceInvalid("permission_basis")
        require_id(self.recorded_by)

    @property
    def permits_text(self) -> bool:
        return self.kind in ("nasa_public_domain", "permission_documented")
