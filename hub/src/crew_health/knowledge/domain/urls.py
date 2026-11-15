from __future__ import annotations

import re

from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound


def _unreserved(value: str) -> str:
    def decode(match: re.Match[str]) -> str:
        code = int(match[0][1:], 16)
        char = chr(code)
        return char if char.isascii() and (char.isalnum() or char in "-._~") else match[0].upper()

    return re.sub(r"%[0-9a-fA-F]{2}", decode, value)


def normalize_url(url: str, *, tracking_parameters: tuple[str, ...], max_chars: int) -> str:
    require_bound(len(url), max_chars)
    if any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise EvidenceInvalid("url")
    if re.search(r"%(?![0-9a-fA-F]{2})", url) is not None:
        raise EvidenceInvalid("url_escape")
    match = re.fullmatch(r"(?i:(https?))://([^/?#]+)([^?#]*)(\?[^#]*)?(#.*)?", url)
    if match is None or "@" in match[2]:
        raise EvidenceInvalid("url")
    scheme, authority, path, query, fragment = match.groups()
    host = re.fullmatch(r"(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9._-]+)(?::([0-9]+))?", authority)
    if host is None:
        raise EvidenceInvalid("url_host")
    scheme, authority = scheme.lower(), host[1].lower()
    if host[2] is not None:
        if len(host[2]) > 5:
            raise EvidenceInvalid("url_port")
        port = int(host[2])
        if not 0 < port <= 65535:
            raise EvidenceInvalid("url_port")
        if (scheme, port) not in (("http", 80), ("https", 443)):
            authority += ":" + str(port)
    parts = []
    if query is not None:
        for part in query[1:].split("&"):
            if _unreserved(part.split("=", 1)[0]) not in tracking_parameters:
                parts.append(_unreserved(part))
    suffix = "?" + "&".join(parts) if parts else ""
    return scheme + "://" + authority + _unreserved(path) + suffix + _unreserved(fragment or "")
