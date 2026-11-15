"""Run plausible regressions against the executable specification, restoring each source in finally."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    cases = (
        ("domain/log.py", "row.delivered_at >= since", "row.delivered_at > since", "test_properties.py"),
        (
            "domain/compose.py",
            "watched: tuple[str, ...] = (unknown,)",
            "watched: tuple[str, ...] = (words.nothing_watched,)",
            "test_properties.py",
        ),
        (
            "domain/render.py",
            "return unknown if value is None else value",
            "return '' if value is None else value",
            "test_compose.py",
        ),
        (
            "application/composer.py",
            'if row is not None and row.state == "frozen":',
            'if row is not None and row.state == "never_frozen":',
            "test_application.py",
        ),
    )
    for relative, original, mutant, suite in cases:
        path = ROOT / "hub/src/crew_health/catchup" / relative
        raw = path.read_bytes()
        source = raw.decode("utf-8")
        if source.count(original) != 1:
            raise RuntimeError(f"mutation anchor is not unique: {relative}")
        try:
            path.write_text(source.replace(original, mutant), encoding="utf-8")
            # Disable bytecode so equal-sized mutants cannot be hidden by a cached .pyc.
            env = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPYCACHEPREFIX": "/private/tmp/e29-mutation-cache",
            }
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    "hub",
                    "pytest",
                    "-c",
                    "hub/pyproject.toml",
                    "-q",
                    str(ROOT / "hub/tests/catchup" / suite),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 1 or " failed" not in result.stdout:
                raise RuntimeError(
                    f"surviving or invalid mutant: {relative}\n{result.stdout}\n{result.stderr}"
                )
            print(f"KILLED {relative}: {original} => {mutant}", flush=True)
        finally:
            path.write_bytes(raw)
    print(f"Mutation: {len(cases)}/{len(cases)} killed", flush=True)


if __name__ == "__main__":
    main()
