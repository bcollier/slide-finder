"""Text to vectors with Voyage AI. One function for documents, one for queries."""

from __future__ import annotations

import httpx

from . import config

URL = "https://api.voyageai.com/v1/embeddings"
BATCH = 100


def _call(texts: list[str], input_type: str) -> list[list[float]]:
    r = httpx.post(
        URL,
        headers={"Authorization": f"Bearer {config.VOYAGE_API_KEY}"},
        json={"input": texts, "model": config.EMBED_MODEL, "input_type": input_type,
              "output_dimension": config.EMBED_DIMS},
        timeout=60,
    )
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def embed_documents(texts: list[str], progress=None) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        out.extend(_call(texts[i:i + BATCH], "document"))
        if progress:
            progress(min(i + BATCH, len(texts)), len(texts))
    return out


def embed_query(text: str) -> list[float]:
    return _call([text], "query")[0]
