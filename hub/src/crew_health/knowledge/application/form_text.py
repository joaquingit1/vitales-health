from __future__ import annotations

from dataclasses import dataclass

from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound, require_hash
from crew_health.knowledge.domain.extraction import Extraction, FormPage
from crew_health.knowledge.domain.registry import require_source_id


@dataclass(frozen=True, slots=True)
class FormBinding:
    source_id: str
    extraction_id: str
    text_sha256: str
    page_map_sha256: str

    def __post_init__(self) -> None:
        require_source_id(self.source_id)
        require_id(self.extraction_id)
        require_hash(self.text_sha256)
        require_hash(self.page_map_sha256)


class FormTextService:
    """Saved text only; construction requires the candidate's immutable form pins."""

    def __init__(
        self,
        release_id: str,
        bindings: tuple[FormBinding, ...],
        extractions: tuple[Extraction, ...],
        *,
        settings: HubSettings,
    ) -> None:
        self.release_id = require_id(release_id)
        self._settings = settings
        require_bound(len(bindings), settings.integer("knowledge.registry.max_rows"))
        require_bound(len(extractions), settings.integer("knowledge.registry.max_rows"))
        extracted = {e.extraction_id: e for e in extractions}
        if len(extracted) != len(extractions) or len({b.source_id for b in bindings}) != len(bindings):
            raise EvidenceInvalid("duplicate_form")
        self._pages: dict[str, tuple[FormPage, ...]] = {}
        total = 0
        for binding in bindings:
            source = extracted.get(binding.extraction_id)
            if source is None or (source.text_sha256, source.page_map_sha256) != (
                binding.text_sha256,
                binding.page_map_sha256,
            ):
                raise EvidenceInvalid("form_pin")
            require_bound(len(source.pages), settings.integer("knowledge.forms.max_pages"))
            pages = source.page_texts()
            for page in pages:
                require_bound(len(page.text.encode("utf-8")), self._bytes("knowledge.forms.max_page_bytes"))
            total += len(source.text.encode("utf-8"))
            require_bound(total, self._bytes("knowledge.bundle.max_member_bytes"))
            self._pages[binding.source_id] = pages

    def _bytes(self, key: str) -> int:
        quantity = self._settings.quantity(key)
        if quantity.unit.code != "By" or quantity.value != quantity.value.to_integral_value():
            raise EvidenceInvalid("byte_setting")
        return int(quantity.value)

    def pages(self, source_id: str) -> tuple[FormPage, ...] | None:
        return self._pages.get(source_id)

    def quote_on_page(self, source_id: str, page: int, quote: str) -> bool:
        if type(page) is not int or page < 1:
            raise EvidenceInvalid("form_page")
        require_bound(len(quote), self._settings.integer("knowledge.passage.max_chars"))
        if not quote:
            return False
        return any(p.page == page and quote in p.text for p in self._pages.get(source_id, ()))
