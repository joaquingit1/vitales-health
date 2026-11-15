"""CONTRACTS 12: reject inconsistent simulated schedules at activation."""

import re

from crew_health.contracts_gen.enums import ConfigName
from crew_health.contracts_gen.schemas.config_environment_profiles_schema import EnvironmentProfilesJson
from crew_health.groundlink.application.config import profile_link
from crew_health.groundlink.domain.link import window_findings
from crew_health.kernel.config import CandidateRelease, Finding


class LinkScheduleCheck:
    check_id = "groundlink.sim_schedule"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        document = candidate.documents.get(ConfigName.ENVIRONMENT_PROFILES)
        if document is None:
            return ()
        profiles = EnvironmentProfilesJson.model_validate_json(document.raw)
        findings: list[Finding] = []
        for i, profile in enumerate(profiles.profiles):
            for message in window_findings(profile_link(profile).windows):
                index = re.search(r"window (\d+)", message)
                if index is not None:
                    findings.append(
                        Finding(
                            self.check_id,
                            ConfigName.ENVIRONMENT_PROFILES,
                            f"/profiles/{i}/link/sim_schedule/{index.group(1)}",
                            message,
                        )
                    )
        return tuple(findings)
