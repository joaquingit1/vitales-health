"""Fixtures of the habitat adapter tests; helpers live in support.py."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from support import NASA, Rig, make_rig


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = make_rig(tmp_path)
    yield made
    made.queue.close()


@pytest.fixture
def small_rig(tmp_path: Path) -> Iterator[Rig]:
    made = make_rig(
        tmp_path, queue_max=6, batch_max_readings=3, retry_max=3, batch_interval_s=1, retry_max_backoff_s=4
    )
    yield made
    made.queue.close()


@pytest.fixture
def twin_data(tmp_path: Path) -> Path:
    """The NASA files the twin replays, gathered in one directory (the twin reads files by relative name)."""
    target = tmp_path / "twin_data"
    (target / "iss-co2-symptom-reports-2001-2009").mkdir(parents=True)
    shutil.copy(NASA / "habitat-environment" / "eda_rr1_telemetry.csv", target)
    shutil.copy(NASA / "radiation" / "radlab_dostel_sample.csv", target)
    shutil.copy(NASA / "radiation" / "radlab_iss_2017-09_spe.csv", target)
    shutil.copy(NASA / "acoustics" / "iss_acoustic_levels.csv", target)
    shutil.copy(
        NASA / "cases" / "iss-co2-symptom-reports-2001-2009" / "co2_symptom_reports.csv",
        target / "iss-co2-symptom-reports-2001-2009",
    )
    return target
