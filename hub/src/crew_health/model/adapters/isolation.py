from pathlib import Path

from crew_health.model.domain.release import ModelRefused


def refuse_holdout(path: Path, *, dev: Path, holdout: Path) -> Path:
    candidate = path.resolve(strict=True)
    if candidate.is_relative_to(holdout.resolve()) or not candidate.is_relative_to(dev.resolve()):
        raise ModelRefused("holdout_path_refused")
    return candidate
