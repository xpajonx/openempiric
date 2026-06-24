from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def tokenize(text: str) -> tuple[str, ...]:
    normalized = normalize_text(text)
    if not normalized:
        return ()
    return tuple(token for token in re.findall(r"\w+", normalized, flags=re.UNICODE) if token)


def unique_tokens(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokenize(text):
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def contains_phrase(haystack: str, needle: str) -> bool:
    normalized_haystack = normalize_text(haystack)
    normalized_needle = normalize_text(needle)
    if not normalized_haystack or not normalized_needle:
        return False
    return normalized_needle in normalized_haystack


def shared_tokens(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    right_set = set(right)
    return tuple(token for token in left if token in right_set)


def lexical_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    return len(shared) / max(len(set(left)), len(set(right)))

