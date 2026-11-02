"""ICP (ideal-customer-profile) similarity via sentence embeddings + FAISS.

Embeds a small set of ICP descriptions into a FAISS index; for a new lead we
embed its text and return the top cosine similarity — a "does this look like our
best customers?" signal that nudges the fit score. Heavy deps (sentence-
transformers, faiss) are imported lazily, so this only loads when used.
"""

from __future__ import annotations

from typing import Any

from ..logging import get_logger

log = get_logger(__name__)

# Seed descriptions of the ideal customer. Swap these for your own to retarget.
ICP_SEEDS: list[str] = [
    "B2B SaaS company with 50 to 500 employees evaluating tools for their sales team",
    "Fast-growing startup that wants to automate inbound lead response and qualification",
    "Mid-market revenue team comparing vendors and asking about pricing, demos, and onboarding",
    "Sales operations leader looking to cut response time and route leads into a CRM",
    "Marketing team at a growth-stage company that needs to qualify high volumes of inbound",
]
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class IcpIndex:
    """A FAISS index over ICP seed embeddings; scores lead↔ICP similarity."""

    def __init__(self, model: Any, index: Any) -> None:
        self._model = model
        self._index = index

    @classmethod
    def build(cls, seeds: list[str] | None = None) -> IcpIndex:
        import faiss
        from sentence_transformers import SentenceTransformer

        texts = seeds or ICP_SEEDS
        model = SentenceTransformer(_EMBED_MODEL)
        emb = model.encode(texts, normalize_embeddings=True)
        index = faiss.IndexFlatIP(emb.shape[1])  # cosine sim via normalized inner product
        index.add(emb)
        log.info("icp.built", seeds=len(texts), dim=int(emb.shape[1]))
        return cls(model, index)

    def similarity(self, text: str) -> float:
        """Top-1 cosine similarity (0-1) of `text` to the ICP seeds."""
        if not text.strip():
            return 0.0
        emb = self._model.encode([text], normalize_embeddings=True)
        scores, _ = self._index.search(emb, 1)
        return max(0.0, min(1.0, float(scores[0][0])))
