import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemorySettings
from crew_health.knowledge.adapters.registry_file import parse_json
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.urls import normalize_url
from crew_health.knowledge.module import register, setting_specs


@pytest.mark.trace("T-E10-07", "E10-02", "I2")
@given(st.sampled_from(("HTTP", "http", "https")), st.text(alphabet="abcXYZ012", min_size=1, max_size=40))
def test_normalization_is_idempotent(scheme: str, path: str) -> None:
    url = f"{scheme}://EXAMPLE.COM/{path}?utm_source=x&q=A%2FB#page=2"
    normalized = normalize_url(url, tracking_parameters=("utm_source",), max_chars=200)
    assert normalize_url(normalized, tracking_parameters=("utm_source",), max_chars=200) == normalized
    assert path in normalized
    assert "q=A%2FB" in normalized
    assert normalized.endswith("#page=2")


@pytest.mark.trace("T-E10-07", "I2")
def test_meaningful_url_distinctions_survive() -> None:
    urls = (
        "https://example.com/A",
        "https://example.com/a",
        "https://example.com/a/",
        "https://example.com/a?q=1",
        "https://example.com/a?q=2",
    )
    assert len({normalize_url(u, tracking_parameters=(), max_chars=200) for u in urls}) == len(urls)
    assert (
        normalize_url("HTTP://Example.COM:80/%7eA", tracking_parameters=(), max_chars=200)
        == "http://example.com/~A"
    )


@pytest.mark.trace("T-E10-07", "T-E10-55", "I8")
@pytest.mark.parametrize(
    "url",
    (
        "",
        "file:///private/data",
        "https://user:pass@example.com/a",
        "https://a/\n",
        "https://a/%GG",
        "https://a:100000/",
        "https://a:0/",
    ),
)
def test_unsafe_or_malformed_url_rejected(url: str) -> None:
    with pytest.raises(EvidenceInvalid):
        normalize_url(url, tracking_parameters=(), max_chars=200)


@pytest.mark.trace("T-E10-55", "I8")
@pytest.mark.parametrize("raw", (b'{"a":1,"a":2}', b"[NaN]", b"[Infinity]", b"[-Infinity]", b"{", b"\xff"))
def test_json_rejects_duplicate_nonfinite_malformed_and_nonunicode(raw: bytes) -> None:
    with pytest.raises(EvidenceInvalid):
        parse_json(raw, max_bytes=100, max_depth=5)


@pytest.mark.trace("T-E10-55", "I8")
@given(st.integers(min_value=1, max_value=15))
def test_json_depth_bound_precedes_allocation(depth: int) -> None:
    raw = b"[" * depth + b"0" + b"]" * depth
    assert parse_json(raw, max_bytes=len(raw), max_depth=depth) is not None
    with pytest.raises(EvidenceInvalid):
        parse_json(b"[" + raw + b"]", max_bytes=len(raw) + 2, max_depth=depth)
    with pytest.raises(EvidenceInvalid):
        parse_json(raw, max_bytes=len(raw) - 1, max_depth=depth)


@pytest.mark.trace("T-E10-55", "I8")
def test_json_brackets_inside_escaped_strings_do_not_increase_depth() -> None:
    assert parse_json(b'{"text":"[\\"{]"}', max_bytes=100, max_depth=1) == {"text": '["{]'}


@pytest.mark.trace("T-E10-56", "D7", "I8")
def test_registered_setting_edit_changes_admission(settings: MemorySettings) -> None:
    values = dict(settings.values)
    values["knowledge.bundle.max_json_depth"] = 1
    changed = MemorySettings(setting_specs(), values)
    raw = b"[[0]]"
    assert parse_json(raw, max_bytes=100, max_depth=settings.integer("knowledge.bundle.max_json_depth")) == [
        [0]
    ]
    with pytest.raises(EvidenceInvalid):
        parse_json(raw, max_bytes=100, max_depth=changed.integer("knowledge.bundle.max_json_depth"))
    values["knowledge.bundle.max_json_depth"] = 0
    with pytest.raises(ProblemError):
        MemorySettings(setting_specs(), values)


@pytest.mark.trace("T-E10-56", "T-E10-60", "I8", "C20")
def test_module_declares_owned_settings_and_prefixes() -> None:
    registry = DeclarationRegistry(max_ports=10, max_hooks=100)
    register(registry)
    assert len(registry.declarations["settings"]) == len(setting_specs())
    assert len(registry.declarations["prefixes"]) == 6


@pytest.mark.trace("T-E10-55", "I5", "I8")
@pytest.mark.parametrize("raw", (b"1e999", b"-1e999", b'"\\ud800"', b'{"a":"\\udfff"}', b'{"\\ud800":0}'))
def test_json_rejects_overflow_and_unpaired_surrogates(raw: bytes) -> None:
    with pytest.raises(EvidenceInvalid):
        parse_json(raw, max_bytes=100, max_depth=5)


@pytest.mark.trace("T-E10-55", "I8")
def test_json_keeps_valid_surrogate_pairs_and_finite_numbers() -> None:
    assert parse_json(b'"\\ud83d\\ude80"', max_bytes=100, max_depth=5) == "🚀"
    assert parse_json(b"1e2", max_bytes=100, max_depth=5) == 100


@pytest.mark.trace("T-E10-55", "I8")
@pytest.mark.parametrize("encoding", ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"))
def test_json_accepts_only_utf8_before_scanning_depth(encoding: str) -> None:
    raw = '["public", {"text": "source"}]'.encode(encoding)
    with pytest.raises(EvidenceInvalid):
        parse_json(raw, max_bytes=len(raw), max_depth=5)


@pytest.mark.trace("T-E10-55", "I8")
def test_json_integer_conversion_failure_is_typed() -> None:
    raw = b"9" * 10000
    with pytest.raises(EvidenceInvalid):
        parse_json(raw, max_bytes=len(raw), max_depth=1)
