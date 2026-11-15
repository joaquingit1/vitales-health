from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.domain.errors import BenchError


def verify_partitions(root: Path, *, max_file_bytes: int) -> None:
    names = ("fingerprints/holdout.fp.json", "partitions/canaries.json", "partitions/manifest.json",
             "partitions/stratification.json")
    try:
        marker = vault_path(root, "partitions/FROZEN.sha256")
        with marker.open("rb") as stream:
            data = stream.read(max_file_bytes + 1)
        if len(data) > max_file_bytes:
            raise BenchError("integrity")
        lines = data.decode("utf-8").splitlines()
        if len(lines) != len(names):
            raise BenchError("integrity")
        for line, name in zip(lines, names, strict=True):
            digest, separator, recorded = line.partition("  ")
            if not separator or recorded != name:
                raise BenchError("integrity")
            with vault_path(root, name).open("rb") as stream:
                payload = stream.read(max_file_bytes + 1)
            if len(payload) > max_file_bytes or sha256(payload).hexdigest() != digest:
                raise BenchError("integrity")
    except OSError as error:
        raise BenchError("target_unreadable", exit_code=6) from error
    except (ValueError, UnicodeError) as error:
        raise BenchError("integrity") from error
