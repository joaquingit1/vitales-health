"""Optional environment facts for one caller-owned snapshot."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from crew_health.guidance.application.ports import Known, SkillFacts
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.plans.application.ports import StockLevel, StockReader, SupplyCoverPort, SupplyNeed
from crew_health.plans.application.release import kit_for, return_option_present
from crew_health.plans.domain.availability import EnvContext, Person
from crew_health.plans.domain.availability import StockLevel as DomainStockLevel
from crew_health.plans.domain.model import SupplyNeed as DomainSupplyNeed
from crew_health.roles.application import ports as roles_ports


class EnvironmentReader:
    def __init__(
        self,
        skills: SkillFacts | None,
        roster: roles_ports.RosterPort | None,
        stock: StockReader | None,
        cover: SupplyCoverPort | None,
    ) -> None:
        self.skills, self.roster, self.stock, self.cover = skills, roster, stock, cover

    def context(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        environment_id: str,
        subject_person_id: str | None,
        needs: Sequence[DomainSupplyNeed],
    ) -> EnvContext:
        stock = self._stock(environment_id)
        levels = (
            None
            if stock is None
            else {
                key: DomainStockLevel(row.supply_id, row.aboard, row.quantity_now)
                for key, row in stock.items()
            }
        )
        return EnvContext(
            environment_id,
            kit_for(view.release, environment_id),
            levels,
            self._people(view, subject_person_id),
            subject_person_id,
            self._cover(environment_id, needs, stock),
            return_option_present(view.release, environment_id),
        )

    def _stock(self, environment_id: str) -> Mapping[str, StockLevel] | None:
        if self.stock is None:
            return None
        try:
            return self.stock.stock(environment_id)
        except ProblemError:
            raise
        except Exception:
            logging.getLogger(__name__).warning("plans_stock_unavailable")
            return None

    def _people(
        self, view: ReadSnapshot | UnitOfWork, subject_person_id: str | None
    ) -> tuple[Person, ...] | None:
        if self.skills is None:
            return None
        try:
            facts = self.skills.operators(view, subject_person_id, None)
            if not isinstance(facts, Known):
                return None
            names = self._names(view)
            return tuple(
                Person(
                    fact.person_id,
                    names.get(fact.person_id),
                    fact.roster_order,
                    fact.officer_rank,
                    fact.awake and fact.on_site and not fact.on_eva,
                    fact.skill_freshness,
                )
                for fact in facts.value
            )
        except ProblemError:
            raise
        except Exception:
            logging.getLogger(__name__).warning("plans_people_unavailable")
            return None

    def _names(self, view: ReadSnapshot | UnitOfWork) -> Mapping[str, str]:
        if self.roster is None:
            return {}
        try:
            return {person.person_id: person.display_name for person in self.roster.crew(view.now)}
        except ProblemError:
            raise
        except Exception:
            logging.getLogger(__name__).warning("plans_roster_unavailable")
            return {}

    def _cover(
        self,
        environment_id: str,
        needs: Sequence[DomainSupplyNeed],
        stock: Mapping[str, StockLevel] | None,
    ) -> Mapping[tuple[str, str], int | None]:
        if self.cover is None or stock is None:
            return {}
        public = tuple(SupplyNeed(n.plan_id, n.step_id, n.supply_id, n.per_use) for n in needs)
        try:
            return self.cover.uses_after_one(environment_id, public, stock)
        except ProblemError:
            raise
        except Exception:
            logging.getLogger(__name__).warning("plans_cover_unavailable")
            return {}
