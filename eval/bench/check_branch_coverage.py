"""BUILD-CHARTER section 4 requires branch coverage independently of statement coverage."""

from __future__ import annotations

import sys
from pathlib import Path

from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.domain.documents import integer, object_map


def main() -> int:
    config_dir, report_path = Path(sys.argv[1]), Path(sys.argv[2])
    settings = BenchSettings(load_document(config_dir / "bench-settings.json", max_file_bytes=1 << 16))
    report = read_json(report_path, max_file_bytes=settings.integer("bench.leak.max_file_bytes"))
    totals = object_map(report["totals"])
    total = integer(totals["num_branches"])
    covered = integer(totals["covered_branches"])
    print(f"Domain and application branches: {covered}/{total}")
    return 0 if total > 0 and covered * 100 >= total * 95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
