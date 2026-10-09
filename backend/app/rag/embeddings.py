"""Embedding providers for the vector leg of hybrid search.

Claude does not provide an embeddings endpoint; Voyage AI's multilingual models
are the default. Any provider works if it implements `Embedder`.
"""

from typing import Literal, Protocol

import httpx


class Embedder(Protocol):
    dim: int

    async def embed(self, texts: list[str], input_type: Literal["document", "query"]) -> list[list[float]]: ...


class VoyageEmbedder:
    URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str, dim: int, batch_size: int = 64):
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.batch_size = batch_size

    async def embed(self, texts: list[str], input_type: Literal["document", "query"]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i : i + self.batch_size]
                resp = await client.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"input": batch, "model": self.model, "input_type": input_type},
                )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                out.extend(d["embedding"] for d in data)
        return out
