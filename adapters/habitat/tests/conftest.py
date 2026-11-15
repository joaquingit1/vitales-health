"""Fixtures of the habitat adapter tests; helpers live in support.py."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from support import Rig, make_rig


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
