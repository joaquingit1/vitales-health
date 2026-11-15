from __future__ import annotations

import json
import struct
from pathlib import Path

import gguf
import numpy as np

from verify_tensors import reorder, transformed, verify

CONFIG = {
    "num_hidden_layers": 1,
    "linear_num_key_heads": 2,
    "linear_num_value_heads": 4,
    "linear_key_head_dim": 2,
    "linear_value_head_dim": 2,
}


def test_grouped_value_heads_are_tiled_with_known_order() -> None:
    actual = reorder(np.arange(8), 0, 2, 4, 2)
    assert actual.tolist() == [0, 1, 4, 5, 2, 3, 6, 7]


def test_norm_and_state_parameterization_have_independent_golden_values() -> None:
    values, steps = transformed(
        "model.layers.0.input_layernorm.weight", np.array([0.5, -0.5], dtype=np.float32), CONFIG
    )
    assert values.tolist() == [1.5, 0.5]
    assert steps == ["RMSNorm residual scale +1, float32"]
    values, steps = transformed("model.layers.0.linear_attn.A_log", np.zeros(4, dtype=np.float32), CONFIG)
    assert values.tolist() == [-1.0, -1.0, -1.0, -1.0]
    assert "A=-exp(A_log), float32" in steps


def fixture(tmp_path: Path, *, wrong_norm: bool) -> tuple[Path, Path]:
    checkpoint = tmp_path / "source"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(json.dumps({"text_config": CONFIG}), encoding="utf-8")
    arrays = {
        "lm_head.weight": np.array([[1, 2], [3, 4]], dtype=np.float32),
        "model.language_model.norm.weight": np.array([0, 1], dtype=np.float32),
    }
    blobs = []
    header = {}
    offset = 0
    for name, array in arrays.items():
        raw = (array.view(np.uint32) >> 16).astype("<u2").tobytes()
        header[name] = {
            "dtype": "BF16",
            "shape": list(array.shape),
            "data_offsets": [offset, offset + len(raw)],
        }
        blobs.append(raw)
        offset += len(raw)
    encoded = json.dumps(header, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 8)
    (checkpoint / "model.safetensors").write_bytes(
        struct.pack("<Q", len(encoded)) + encoded + b"".join(blobs)
    )
    artifact = tmp_path / "converted.gguf"
    writer = gguf.GGUFWriter(str(artifact), "qwen35")
    writer.add_tensor(
        "output.weight",
        (arrays["lm_head.weight"].view(np.uint32) >> 16).astype(np.uint16),
        raw_dtype=gguf.GGMLQuantizationType.BF16,
    )
    writer.add_tensor("output_norm.weight", np.array([2, 3] if wrong_norm else [1, 2], dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return checkpoint, artifact


def test_verifier_checks_every_text_tensor_against_safetensors_source(tmp_path: Path) -> None:
    checkpoint, artifact = fixture(tmp_path, wrong_norm=False)
    result = verify(checkpoint, artifact)
    assert result["verdict"] == "pass"
    assert result["text_tensors"] == 2 and result["exact_text_tensors"] == 2
    assert result["failures"] == []


def test_wrong_normalization_conversion_is_rejected(tmp_path: Path) -> None:
    checkpoint, artifact = fixture(tmp_path, wrong_norm=True)
    result = verify(checkpoint, artifact)
    assert result["verdict"] == "fail"
    assert result["failures"] == [{"tensor": "model.language_model.norm.weight", "reason": "tensor mismatch"}]


def test_same_bytes_with_wrong_tensor_shape_are_rejected(tmp_path: Path) -> None:
    checkpoint, artifact = fixture(tmp_path, wrong_norm=False)
    changed = tmp_path / "wrong-shape.gguf"
    writer = gguf.GGUFWriter(str(changed), "qwen35")
    # The exact same source bytes with changed geometry are not the same tensor.
    bits = (np.array([[1, 2, 3, 4]], dtype=np.float32).view(np.uint32) >> 16).astype(np.uint16)
    writer.add_tensor("output.weight", bits, raw_dtype=gguf.GGMLQuantizationType.BF16)
    writer.add_tensor("output_norm.weight", np.array([1, 2], dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    result = verify(checkpoint, changed)
    assert result["verdict"] == "fail"
