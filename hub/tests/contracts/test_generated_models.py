"""The generator's post-pass keeps datamodel-codegen's allOf-over-oneOf models type-correct under mypy --strict
without changing what they validate (charter section 8 gate: zero mypy warnings)."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from crew_health.contracts_gen.build import flatten_overridden_bases
from crew_health.contracts_gen.schemas.plan_view_schema import PlanView4, PlanView5
from crew_health.contracts_gen.schemas.role_assignment_schema import RoleAssignment4, RoleAssignment5

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.6")]

BRANCHES = """from pydantic import BaseModel, ConfigDict


class Branch1(BaseModel):
    left: str | None = None
    right: None = None


class Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: str | None
    right: str | None


class Combined(Branch1, Base):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: str | None
    right: str | None
"""


def test_combined_model_redeclaring_every_branch_field_is_rebased(tmp_path: Path) -> None:
    generated = tmp_path / "combined_schema.py"
    generated.write_text(BRANCHES, encoding="utf-8")
    flatten_overridden_bases(generated)
    assert "class Combined(BaseModel):\n" in generated.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "change",
    [
        ("    right: None = None\n", "    right: None = None\n    only_here: int\n"),
        (
            "    right: None = None\n",
            "    right: None = None\n\n    def check(self) -> None:\n        pass\n",
        ),
        (
            '        extra="forbid",\n    )\n    left: str | None\n    right: str | None\n\n\nclass Combined',
            '        extra="allow",\n    )\n    left: str | None\n    right: str | None\n\n\nclass Combined',
        ),
    ],
)
def test_bases_that_add_fields_behavior_or_config_are_kept(tmp_path: Path, change: tuple[str, str]) -> None:
    generated = tmp_path / "combined_schema.py"
    generated.write_text(BRANCHES.replace(change[0], change[1], 1), encoding="utf-8")
    flatten_overridden_bases(generated)
    assert "class Combined(Branch1, Base):\n" in generated.read_text(encoding="utf-8")


def test_generated_plan_view_and_role_assignment_carry_no_conflicting_branch_base() -> None:
    for model in (PlanView4, PlanView5, RoleAssignment4, RoleAssignment5):
        assert model.__bases__ == (BaseModel,)
        assert model.model_config.get("extra") == "forbid"
