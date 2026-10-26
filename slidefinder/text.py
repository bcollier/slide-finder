"""Building the text that gets embedded: slide cards and transcript chunks."""

from __future__ import annotations

import re

from . import config

_SENT = re.compile(r"(?<=[.!?])\s+")


def card_text(course_name: str, session_title: str, slide_number: int, title: str,
              text: str, notes: str, description: str = "") -> str:
    """One paragraph per slide: where it is, what it says, what the notes add, what it shows."""
    body = text.strip()
    if title and body.lower().startswith(title.lower()):
        body = body[len(title):].strip()
    parts = [f"{course_name}, {session_title}, slide {slide_number}: {title}".strip(": ")]
    if body:
        parts.append(body)
    if description:
        parts.append(description.strip())
    if notes.strip():
        parts.append("Speaker notes: " + " ".join(notes.split()[:220]))
    return "\n".join(parts)


def said_chunks(title: str, transcript: str, max_words: int = config.SAID_CHUNK_WORDS) -> list[str]:
    """Split what I said over a slide into ~max_words chunks on sentence boundaries."""
    words_total = transcript.split()
    if not words_total:
        return []
    chunks, cur, n = [], [], 0
    for sent in _SENT.split(transcript.strip()):
        w = len(sent.split())
        if cur and n + w > max_words:
            chunks.append(" ".join(cur))
            cur, n = [], 0
        cur.append(sent)
        n += w
    if cur:
        chunks.append(" ".join(cur))
    # A tiny tail chunk is better merged into the previous one.
    if len(chunks) > 1 and len(chunks[-1].split()) < 25:
        tail = chunks.pop()
        chunks[-1] = chunks[-1] + " " + tail
    prefix = f"[{title}] " if title else ""
    return [prefix + c for c in chunks]
