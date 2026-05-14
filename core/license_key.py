from __future__ import annotations

import re


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_license_key(raw_key: str) -> str:
    if raw_key is None:
        return ""
    value = _WHITESPACE_RE.sub("", str(raw_key).strip())
    return value.upper()


def get_license_key_prefix(normalized_key: str) -> str:
    if not normalized_key:
        return ""
    parts = normalized_key.split("-")
    if len(parts) >= 2 and parts[0]:
        return "-".join(parts[:2])[:12]
    return normalized_key[:12]


def mask_license_key(raw_key: str) -> str:
    normalized = normalize_license_key(raw_key)
    if not normalized:
        return ""
    parts = normalized.split("-")
    if len(parts) >= 3:
        return "-".join([parts[0], "****", *["****" for _ in parts[2:-1]], parts[-1]])
    if len(normalized) <= 8:
        return "****"
    return f"{normalized[:4]}****{normalized[-4:]}"
