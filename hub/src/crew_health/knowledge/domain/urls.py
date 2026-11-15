from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from crew_health.knowledge.domain.errors import KnowledgeInvalid


def normalize_url(url: str, tracking_parameters: tuple[str, ...] = ()) -> str:
    if not url or len(url) > 8192 or any(ord(c) < 33 for c in url):
        raise KnowledgeInvalid("invalid_url")
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise KnowledgeInvalid("invalid_url") from None
    if parts.scheme.lower() not in {"http", "https"} or not host or parts.username or parts.password:
        raise KnowledgeInvalid("invalid_url")
    scheme = parts.scheme.lower()
    netloc = host.lower()
    if ":" in netloc:
        netloc = "[" + netloc + "]"
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        netloc += ":" + str(port)

    def unreserved(match: re.Match[str]) -> str:
        char = chr(int(match[0][1:], 16))
        return char if char.isascii() and (char.isalnum() or char in "-._~") else match[0].upper()

    path = re.sub(r"%[0-9a-fA-F]{2}", unreserved, parts.path)
    query = parts.query
    if tracking_parameters:
        query = urlencode(
            [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in tracking_parameters]
        )
    return urlunsplit((scheme, netloc, path, query, ""))
