"""Packages the station resources into the wheel (R25, DEPLOYMENT 7.1): the frozen contracts, the repository's
configuration documents and the offline package artifacts the hub validates against, under crew_health/resources.
crew_health.app.station.HubResources reads them from there in an installed hub. They come from the repository
around this project, so a wheel is built from a checkout; an editable install reads the checkout directly."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

PROJECT = Path(__file__).resolve().parent
REPOSITORY = PROJECT.parent
# Each entry: a source directory under the repository, its target under crew_health/resources, and the files.
RESOURCES = (
    ("contracts", "contracts", ("SHA256SUMS", "api/**/*", "reference/**/*", "decision-overlay/*")),
    ("config", "config", ("*.json",)),
    # The D12 overlay is verified against the accepted decisions at every start.
    ("docs/requests", "docs/requests", ("COORDINATOR-DECISIONS.md",)),
    (
        "docs/product",
        "product",
        (
            "package-manifest.json",
            "condition-assessments-119.json",
            "input-dictionary.json",
            "sources.csv",
            "NASA-SOURCES.md",
            "DEMO-CONDITIONS.md",
            "RESEARCH-ANNEX.md",
            "environment-hazards.json",
            "routine-schedule.json",
            "responder-flows.json",
            # Check 14 quotes plan steps against the saved CLIFF forms.
            "cliff/forms/*.json",
        ),
    ),
)


class BuildWithResources(build_py):
    def run(self) -> None:
        super().run()
        if getattr(self, "editable_mode", False):
            return
        target = Path(self.build_lib) / "crew_health" / "resources"
        for source, destination, patterns in RESOURCES:
            root = REPOSITORY / source
            files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
            if not files:
                raise RuntimeError("station resources missing: " + source)
            for path in files:
                copy = target / destination / path.relative_to(root)
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, copy)


setup(cmdclass={"build_py": BuildWithResources})
