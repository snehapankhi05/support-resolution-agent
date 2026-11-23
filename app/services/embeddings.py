"""Local 384-dimension embeddings. Tests pass a stub and do not download a model."""

import os
from typing import Protocol

EMBEDDING_DIM = 384
MODEL_NAME = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one 384-float vector per text, in the same order."""


class StubEmbedder:
    """Deterministic vectors for tests. The same text always maps to the same vector."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            stretched = (digest * ((EMBEDDING_DIM // len(digest)) + 1))[:EMBEDDING_DIM]
            vectors.append([byte / 255.0 for byte in stretched])
        return vectors


class FastEmbedder:
    """ONNX build of bge-small-en-v1.5. The weights download on first use."""

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=MODEL_NAME)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for row in self._model.embed(texts):
            values = [float(item) for item in row]
            if len(values) != EMBEDDING_DIM:
                raise ValueError("embedding dimension must be 384")
            vectors.append(values)
        return vectors


_live: FastEmbedder | None = None


def get_policy_embedder() -> Embedder | None:
    """Return the local model only when POLICY_EMBEDDINGS=1. Otherwise search stays lexical."""
    global _live
    if os.environ.get("POLICY_EMBEDDINGS") != "1":
        return None
    if _live is None:
        _live = FastEmbedder()
    return _live
