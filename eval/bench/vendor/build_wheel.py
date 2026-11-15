"""Package inspected upstream bytes without editing the official scorer."""

from __future__ import annotations

import base64
import csv
import io
import json
import py_compile
import sys
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent


def source_files() -> dict[str, bytes]:
    manifest = json.loads((ROOT / "SOURCE.json").read_text(encoding="utf-8"))
    files: dict[str, bytes] = {}
    expected = {
        "healthbench_eval.py",
        "common.py",
        "types.py",
        "sampler/chat_completion_sampler.py",
        "LICENSE",
    }
    if set(manifest["files"]) != expected:
        raise ValueError("unexpected official source inventory")
    for name in sorted(expected):
        raw = (ROOT / "upstream" / (name + ".source")).read_bytes()
        if sha256(raw).hexdigest() != manifest["files"][name]:
            raise ValueError("official source digest mismatch")
        target = (
            "simple_evals/" + name if name != "LICENSE" else "simple_evals-0.0.0+652c89d0.dist-info/LICENSE"
        )
        files[target] = raw
    return files


def metadata() -> dict[str, bytes]:
    prefix = "simple_evals-0.0.0+652c89d0.dist-info/"
    dependencies = ("blobfile", "numpy", "pandas", "jinja2", "requests", "tqdm", "openai")
    data = (
        "Metadata-Version: 2.1\nName: simple-evals\nVersion: 0.0.0+652c89d0\n"
        "Requires-Python: >=3.14,<3.15\nLicense: MIT\n"
    )
    data += "".join("Requires-Dist: " + name + "\n" for name in dependencies)
    return {
        prefix + "METADATA": data.encode("utf-8"),
        prefix
        + "WHEEL": b"Wheel-Version: 1.0\nGenerator: crew-health-bench\nRoot-Is-Purelib: true\nTag: py314-none-any\n",
        "simple_evals/__init__.py": b"",
        "simple_evals/sampler/__init__.py": b"",
        "simple_evals/py.typed": b"",
    }


def bytecode(files: dict[str, bytes]) -> None:
    with TemporaryDirectory(prefix="e18-official-build-") as directory:
        for name, raw in tuple(files.items()):
            if not name.endswith(".py"):
                continue
            source = Path(directory) / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(raw)
            cache = source.parent / "__pycache__" / (source.stem + ".cpython-314.pyc")
            cache.parent.mkdir(exist_ok=True)
            py_compile.compile(
                str(source),
                cfile=str(cache),
                dfile=name,
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
            files[str(cache.relative_to(directory)).replace("\\", "/")] = cache.read_bytes()


def build() -> Path:
    if sys.version_info[:2] != (3, 14):
        raise ValueError("the official wheel uses the repository's Python 3.14 pin")
    files = {**source_files(), **metadata()}
    for path in sorted((ROOT / "stubs").rglob("*.pyi")):
        files["simple_evals/" + path.relative_to(ROOT / "stubs").as_posix()] = path.read_bytes()
    bytecode(files)
    output = io.StringIO(newline="")
    rows = csv.writer(output, lineterminator="\n")
    for name, raw in sorted(files.items()):
        digest = base64.urlsafe_b64encode(sha256(raw).digest()).decode("ascii").rstrip("=")
        rows.writerow((name, "sha256=" + digest, len(raw)))
    record = "simple_evals-0.0.0+652c89d0.dist-info/RECORD"
    rows.writerow((record, "", ""))
    files[record] = output.getvalue().encode("utf-8")
    target = ROOT / "simple_evals-0.0.0+652c89d0-py314-none-any.whl"
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, raw)
    return target


if __name__ == "__main__":
    print(build())
