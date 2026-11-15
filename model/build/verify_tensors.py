"""Compare Huatuo source tensors with BF16 GGUF using independently written documented transforms.

No converter implementation is imported. Names come from gguf's public tensor map. Only NumPy exp
rounding may differ by one float32 ULP from Torch's exp. Every such difference is counted separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import gguf
import numpy as np


def reorder(data: Any, axis: int, k: int, v: int, head: int) -> Any:
    indices = np.arange(v * head).reshape(k, v // k, head).transpose(1, 0, 2).reshape(-1)
    return np.take(data, indices, axis=axis)


def transformed(name: str, data: Any, config: dict[str, Any]) -> tuple[Any, list[str]]:
    steps = []
    k, v = int(config["linear_num_key_heads"]), int(config["linear_num_value_heads"])
    hk, hv = int(config["linear_key_head_dim"]), int(config["linear_value_head_dim"])
    if k != v and "linear_attn." in name:
        if ".in_proj_qkv." in name:
            split = 2 * k * hk
            data = np.concatenate((data[:split], reorder(data[split:], 0, k, v, hv)), axis=0)
            steps.append("V rows grouped to tiled")
        elif ".in_proj_z." in name:
            data = reorder(data, 0, k, v, hv)
            steps.append("V rows grouped to tiled")
        elif ".in_proj_a." in name or ".in_proj_b." in name:
            data = reorder(data, 0, k, v, 1)
            steps.append("V heads grouped to tiled")
        elif ".A_log" in name or ".dt_bias" in name:
            data = reorder(data, 0, k, v, 1)
            steps.append("V heads grouped to tiled")
        elif ".conv1d." in name:
            data = data.squeeze()
            split = 2 * k * hk
            data = np.concatenate((data[:split], reorder(data[split:], 0, k, v, hv)), axis=0)
            steps.append("Conv V channels grouped to tiled and squeeze")
        elif ".out_proj." in name:
            data = reorder(data, 1, k, v, hv)
            steps.append("V columns grouped to tiled")
    if name.endswith(".A_log"):
        data = -np.exp(data, dtype=np.float32)
        steps.append("A=-exp(A_log), float32")
    elif "conv1d" in name:
        data = data.squeeze()
        steps.append("squeeze")
    elif name.endswith("norm.weight") and not name.endswith("linear_attn.norm.weight"):
        data = data + np.float32(1)
        steps.append("RMSNorm residual scale +1, float32")
    return data, steps


def digest_array(value: Any) -> str:
    flat = np.ascontiguousarray(value).view(np.uint8).reshape(-1)
    digest = hashlib.sha256()
    for start in range(0, flat.size, 1048576):
        digest.update(memoryview(flat[start : start + 1048576]))
    return digest.hexdigest()


def raw_comparison(
    source: Path, artifact: Path, source_offset: int, target_offset: int, size: int
) -> tuple[bool, str, str]:
    left, right = hashlib.sha256(), hashlib.sha256()
    equal = True
    with source.open("rb") as src, artifact.open("rb") as dst:
        src.seek(source_offset)
        dst.seek(target_offset)
        for start in range(0, size, 1048576):
            count = min(1048576, size - start)
            a, b = src.read(count), dst.read(count)
            equal = equal and a == b and len(a) == count
            left.update(a)
            right.update(b)
    return equal, left.hexdigest(), right.hexdigest()


def verify(checkpoint: Path, artifact: Path) -> dict[str, Any]:
    config = json.loads((checkpoint / "config.json").read_text(encoding="utf-8"))["text_config"]
    source = checkpoint / "model.safetensors"
    with source.open("rb") as stream:
        size = struct.unpack("<Q", stream.read(8))[0]
        if not 1 <= size <= 1048576:
            raise ValueError("header bound")
        header = json.loads(stream.read(size))
    names = gguf.get_tensor_name_map(gguf.MODEL_ARCH.QWEN35, int(config["num_hidden_layers"]))
    reader = gguf.GGUFReader(str(artifact))
    targets = {t.name: t for t in reader.tensors}
    rows = []
    skipped = []
    seen = set()
    failures = []
    for original, entry in header.items():
        if original == "__metadata__":
            continue
        if ".visual." in original:
            skipped.append(
                {"tensor": original, "reason": "Vision tower not consumed by this text-only product"}
            )
            continue
        name = original.replace("model.language_model.", "model.")
        mapped = names.get_name(name.replace(".dt_bias", ".dt_proj.bias"), try_suffixes=(".weight", ".bias"))
        if mapped not in targets:
            failures.append({"tensor": original, "reason": "unmapped text tensor"})
            continue
        target = targets[mapped]
        seen.add(mapped)
        dtype = {"BF16": "<u2", "F32": "<f4", "F16": "<f2"}.get(entry["dtype"])
        if dtype is None:
            raise ValueError("source dtype unsupported")
        if (
            entry["dtype"] == "BF16"
            and target.tensor_type == gguf.GGMLQuantizationType.BF16
            and "linear_attn." not in name
        ):
            count = entry["data_offsets"][1] - entry["data_offsets"][0]
            equal, left, right = raw_comparison(
                source, artifact, 8 + size + entry["data_offsets"][0], target.data_offset, count
            )
            equal = equal and tuple(reversed(target.shape)) == tuple(entry["shape"])
            rows.append(
                {
                    "source": original,
                    "gguf": mapped,
                    "transforms": [],
                    "exact": equal,
                    "within_declared_rounding": equal,
                    "max_absolute_difference": 0.0,
                    "expected_sha256": left,
                    "actual_sha256": right,
                }
            )
            if not equal:
                failures.append({"tensor": original, "reason": "tensor mismatch"})
            continue
        data: Any = np.memmap(
            source,
            mode="r",
            dtype=dtype,
            offset=8 + size + entry["data_offsets"][0],
            shape=tuple(entry["shape"]),
        )
        is_float = target.tensor_type == gguf.GGMLQuantizationType.F32
        if is_float:
            data = (
                (data.astype(np.uint32) << 16).view(np.float32)
                if entry["dtype"] == "BF16"
                else data.astype(np.float32)
            )
        expected, steps = transformed(name, data, config)
        if tuple(reversed(target.shape)) != tuple(expected.shape):
            failures.append({"tensor": original, "reason": "tensor shape mismatch"})
            continue
        actual = target.data
        if is_float:
            actual = actual.view(np.float32).reshape(expected.shape)
            equal = np.array_equal(expected, actual)
            close = (
                bool(
                    np.all(
                        np.abs(expected.astype(np.float64) - actual.astype(np.float64))
                        <= np.abs(np.spacing(expected))
                    )
                )
                if name.endswith(".A_log")
                else equal
            )
            max_absolute = float(np.max(np.abs(expected.astype(np.float64) - actual.astype(np.float64))))
        elif target.tensor_type == gguf.GGMLQuantizationType.BF16:
            actual = actual.view(np.uint16).reshape(expected.shape)
            equal = np.array_equal(expected, actual)
            close = equal
            max_absolute = 0.0
        else:
            raise ValueError("Expected BF16 or exact float32 tensor only")
        row = {
            "source": original,
            "gguf": mapped,
            "transforms": steps,
            "exact": bool(equal),
            "within_declared_rounding": bool(close),
            "max_absolute_difference": max_absolute,
            "expected_sha256": digest_array(expected),
            "actual_sha256": digest_array(actual),
        }
        rows.append(row)
        if not close:
            failures.append({"tensor": original, "reason": "tensor mismatch"})
    for name in targets.keys() - seen:
        failures.append({"tensor": name, "reason": "unaccounted GGUF tensor"})
    return {
        "verdict": "pass" if not failures else "fail",
        "measured": True,
        "verification": "source parameterization and layout equivalence, not training or clinical validation",
        "text_tensors": len(rows),
        "skipped_vision_tensors": len(skipped),
        "exact_text_tensors": sum(r["exact"] for r in rows),
        "numeric_transform_correction": "Pinned Qwen3.5 converter materializes -exp(A_log) and RMSNorm+1. Original TDD layout-only equivalence claim is incorrect.",
        "rounding_rule": "At most one float32 ULP for independently calculated exp only, exact equality for all other transformed values",
        "rows": rows,
        "skipped": skipped,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = verify(args.checkpoint, args.artifact)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("verdict", "text_tensors", "skipped_vision_tensors", "exact_text_tensors")
            }
        )
    )
    return 0 if result["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
