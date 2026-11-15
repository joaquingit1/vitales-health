"""CONTRACTS 18: link events are visible to crew, ground and operator sessions."""

from crew_health.kernel.uow import Audience, ReadSnapshot, SessionContext


class GroundLinkAudience:
    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return audience.rule == "groundlink.link" and session.kind in ("crew", "ground", "operator")
