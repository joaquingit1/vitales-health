from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemoryIds, MemorySettings
from crew_health.knowledge.application.form_text import FormBinding, FormTextService
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.extraction import Extraction, PageRange


def saved_text() -> Extraction:
    text = Path("knowledge/inputs/package/NASA-SOURCES.md").read_text(encoding="utf-8")
    boundary = text.index("\n") + 1
    return Extraction.create(
        ids=MemoryIds(identifiers=()),
        document_version_id="dv_a",
        format_version="1",
        text=text,
        pages=(PageRange(1, 0, boundary), PageRange(2, boundary, len(text))),
        extracted_by="editor_a",
        max_chars=len(text),
        max_pages=2,
    )


@pytest.mark.trace("T-E10-62", "C11", "I2", "I5")
def test_quote_verification_is_exact_and_bound_to_saved_extraction(settings: MemorySettings) -> None:
    source = saved_text()
    binding = FormBinding("S01", source.extraction_id, source.text_sha256, source.page_map_sha256)
    reader = FormTextService("evr_a", (binding,), (source,), settings=settings)
    quote = source.page_texts()[0].text
    assert reader.quote_on_page("S01", 1, quote)
    assert not reader.quote_on_page("S01", 2, quote)
    assert not reader.quote_on_page("S01", 1, quote.swapcase())
    assert not reader.quote_on_page("S01", 1, "")
    assert not reader.quote_on_page("S99", 1, quote)
    assert reader.pages("S99") is None
    assert reader.pages("S01") == source.page_texts()


@pytest.mark.trace("T-E10-62", "T-E10-55", "C11", "I8")
def test_form_pins_and_bounds_fail_closed(settings: MemorySettings) -> None:
    source = saved_text()
    binding = FormBinding("S01", source.extraction_id, source.text_sha256, source.page_map_sha256)
    for bindings, extractions in (
        ((replace(binding, text_sha256="0" * 64),), (source,)),
        ((replace(binding, page_map_sha256="0" * 64),), (source,)),
        ((binding,), ()),
        ((binding, binding), (source,)),
        ((binding,), (source, source)),
    ):
        with pytest.raises(EvidenceInvalid):
            FormTextService("evr_a", bindings, extractions, settings=settings)


@pytest.mark.trace("T-E10-62", "T-E10-55", "C11", "I8")
def test_invalid_pages_and_oversize_quotes_fail(settings: MemorySettings) -> None:
    source = saved_text()
    binding = FormBinding("S01", source.extraction_id, source.text_sha256, source.page_map_sha256)
    reader = FormTextService("evr_a", (binding,), (source,), settings=settings)
    for page in (0, -1, True):
        with pytest.raises(EvidenceInvalid):
            reader.quote_on_page("S01", page, source.text[:1])
    with pytest.raises(EvidenceInvalid):
        reader.quote_on_page("S01", 1, "x" * (settings.integer("knowledge.passage.max_chars") + 1))
