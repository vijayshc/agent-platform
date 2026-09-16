"""Pluggable text-chunking strategies for the Knowledge base.

A single entry point, :func:`chunk_text`, dispatches on a *method id* so the
admin UI can offer the choice per upload while the ingestion code stays one
line.  Every strategy honours the same two knobs -- ``chunk_size`` (characters)
and ``chunk_overlap`` -- so retrieval quality is tunable without changing the
caller.

Methods
-------
``recursive``  Split on the coarsest separator that fits (blank line -> line ->
               sentence -> word -> character) and merge the pieces back into
               size-bounded chunks.  Best general-purpose default.
``paragraph``  Pack whole paragraphs; a paragraph larger than the budget is hard
               split.  Matches the historical behaviour.
``sentence``   Pack whole sentences, so a chunk never ends mid-sentence.
``fixed``      Fixed-width windows.  Fastest, but cuts anywhere.
``markdown``   Split on headings and repeat the heading in every part, keeping
               section context with the body.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List

#: Ordered method metadata surfaced to the UI (single source of truth).
METHODS: Dict[str, Dict[str, str]] = {
    "recursive": {
        "label": "Recursive (recommended)",
        "description": "Splits on paragraph, then line, sentence and word boundaries so size is respected without cutting mid-thought.",
    },
    "paragraph": {
        "label": "Paragraph",
        "description": "Keeps paragraphs intact and only splits a paragraph when it exceeds the chunk size.",
    },
    "sentence": {
        "label": "Sentence",
        "description": "Groups whole sentences up to the chunk size; never ends a chunk mid-sentence.",
    },
    "markdown": {
        "label": "Markdown headings",
        "description": "Splits at # headings and repeats the heading on every part, ideal for structured docs.",
    },
    "fixed": {
        "label": "Fixed size",
        "description": "Plain fixed-width windows. Fastest, but can cut words and sentences.",
    },
}

DEFAULT_METHOD = "recursive"

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]*(?:\s+|$)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_RECURSIVE_SEPARATORS = ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", "")


def normalize_method(method: str | None) -> str:
    """Return a valid method id; empty/None falls back to the default.

    An unknown non-empty id raises ``ValueError`` so a bad API payload is
    rejected instead of silently changing how a document is indexed.
    """
    candidate = (method or "").strip().lower()
    if not candidate:
        return DEFAULT_METHOD
    if candidate not in METHODS:
        valid = ", ".join(METHODS)
        raise ValueError(f"Unknown chunking method '{method}'. Valid methods: {valid}")
    return candidate


def validate_size(chunk_size: int | None, default: int) -> int:
    """Coerce/validate the character budget (100..8000)."""
    if chunk_size is None or chunk_size == "":
        return int(default)
    try:
        size = int(chunk_size)
    except (TypeError, ValueError):
        raise ValueError("Chunk size must be a whole number of characters.")
    if size < 100 or size > 8000:
        raise ValueError("Chunk size must be between 100 and 8000 characters.")
    return size


def validate_overlap(chunk_overlap: int | None, chunk_size: int, default: int) -> int:
    """Coerce/validate overlap: never negative and always under half a chunk."""
    if chunk_overlap is None or chunk_overlap == "":
        overlap = int(default)
    else:
        try:
            overlap = int(chunk_overlap)
        except (TypeError, ValueError):
            raise ValueError("Chunk overlap must be a whole number of characters.")
    if overlap < 0:
        raise ValueError("Chunk overlap cannot be negative.")
    ceiling = min(chunk_size // 2, 2000)
    return min(overlap, ceiling)


def chunk_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    method: str = DEFAULT_METHOD,
) -> List[str]:
    """Split ``text`` into overlapping chunks using ``method``.

    ``chunk_size``/``chunk_overlap`` are already-normalised values (see
    :func:`validate_size` / :func:`validate_overlap`); both are character
    counts.
    """
    if not text or not text.strip():
        return []
    size = max(int(chunk_size), 1)
    overlap = max(min(int(chunk_overlap), size - 1), 0)
    strategy: Callable[[str, int, int], List[str]] = _STRATEGIES[normalize_method(method)]
    chunks = [c.strip() for c in strategy(text, size, overlap) if c and c.strip()]
    return chunks


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def _hard_split(text: str, size: int, overlap: int) -> List[str]:
    """Fixed-width windows, optionally overlapping."""
    if size <= 0:
        return []
    step = max(1, size - max(overlap, 0))
    pieces: List[str] = []
    for start in range(0, len(text), step):
        piece = text[start:start + size]
        if piece.strip():
            pieces.append(piece)
        if start + size >= len(text):
            break
    return pieces


def _word_prefix(text: str, overlap: int) -> str:
    """Tail of ``text`` no longer than ``overlap``, snapped to a word boundary."""
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")
    if space != -1 and space < max(1, overlap // 2):
        tail = tail[space + 1:]
    return tail.strip()


def _apply_overlap(chunks: List[str], overlap: int) -> List[str]:
    """Prepend the previous chunk's tail to each chunk after the first."""
    if overlap <= 0 or len(chunks) < 2:
        return chunks
    result = [chunks[0]]
    for previous, current in zip(chunks, chunks[1:]):
        prefix = _word_prefix(previous, overlap)
        result.append(f"{prefix} {current}".strip() if prefix else current)
    return result


def _pack(units: List[str], size: int, overlap: int, joiner: str) -> List[str]:
    """Greedily pack units into <= size chunks, hard-splitting oversized units."""
    chunks: List[str] = []
    current = ""
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        if len(unit) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(unit, size, 0))
            continue
        candidate = f"{current}{joiner}{unit}" if current else unit
        if len(candidate) > size and current:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return _apply_overlap(chunks, overlap)


# --------------------------------------------------------------------------
# strategies
# --------------------------------------------------------------------------

def _fixed(text: str, size: int, overlap: int) -> List[str]:
    return _hard_split(text, size, overlap)


def _paragraph(text: str, size: int, overlap: int) -> List[str]:
    paragraphs = re.split(r"\n\s*\n", text)
    return _pack(paragraphs, size, overlap, joiner="\n\n")


def _sentence(text: str, size: int, overlap: int) -> List[str]:
    sentences = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text)]
    sentences = [s for s in sentences if s]
    if not sentences:
        return _paragraph(text, size, overlap)
    return _pack(sentences, size, overlap, joiner=" ")


def _recursive_split(text: str, size: int, separators) -> List[str]:
    if len(text) <= size or not separators:
        return [text]
    separator, rest = separators[0], separators[1:]
    if separator == "":
        return _hard_split(text, size, 0)
    if separator not in text:
        return _recursive_split(text, size, rest)
    parts = text.split(separator)
    out: List[str] = []
    for index, part in enumerate(parts):
        piece = part + (separator if index < len(parts) - 1 else "")
        if not piece.strip():
            continue
        if len(piece) > size:
            out.extend(_recursive_split(piece, size, rest))
        else:
            out.append(piece)
    return out


def _recursive(text: str, size: int, overlap: int) -> List[str]:
    pieces = _recursive_split(text, size, _RECURSIVE_SEPARATORS)
    return _pack(pieces, size, overlap, joiner="")


def _markdown(text: str, size: int, overlap: int) -> List[str]:
    """Split on ATX headings; repeat the heading in every produced part."""
    sections: List[tuple[str, str]] = []
    heading = ""
    body: List[str] = []
    for line in text.split("\n"):
        if _HEADING_RE.match(line):
            if heading or any(l.strip() for l in body):
                sections.append((heading, "\n".join(body).strip()))
            heading = line.strip()
            body = []
        else:
            body.append(line)
    if heading or any(l.strip() for l in body):
        sections.append((heading, "\n".join(body).strip()))

    if not sections:
        return _recursive(text, size, overlap)

    chunks: List[str] = []
    for heading, body in sections:
        prefix = f"{heading}\n\n" if heading else ""
        if not body:
            chunks.append(heading)
            continue
        body_budget = max(size - len(prefix), 1)
        parts = _pack(_recursive_split(body, body_budget, _RECURSIVE_SEPARATORS), body_budget, 0, joiner="\n\n")
        for part in parts:
            chunks.append(f"{prefix}{part}".strip())
    return _apply_overlap(chunks, overlap)


_STRATEGIES: Dict[str, Callable[[str, int, int], List[str]]] = {
    "fixed": _fixed,
    "paragraph": _paragraph,
    "sentence": _sentence,
    "recursive": _recursive,
    "markdown": _markdown,
}
