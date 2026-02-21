"""
ChromaDB-backed RAG store for semantic job deduplication and retrieval.

Uses sentence-transformers (local, free) for embeddings to avoid API costs.
Collection: "job_descriptions"
"""

from pathlib import Path
from typing import Optional, Dict, Any, List

from ..config import CHROMA_DIR, SEMANTIC_DEDUP_THRESHOLD


class RagStore:
    """ChromaDB vector store wrapper for job description embeddings."""

    COLLECTION_NAME = "job_descriptions"

    def __init__(self):
        self._client = None
        self._collection = None
        self._embedding_fn = None

    def _ensure_initialized(self):
        """Lazy initialization — only import heavy dependencies when needed."""
        if self._client is not None:
            return

        try:
            import chromadb
            from chromadb.utils import embedding_functions
        except ImportError as e:
            raise ImportError(
                "chromadb is required for semantic deduplication. "
                "Install with: pip install chromadb sentence-transformers"
            ) from e

        Path(CHROMA_DIR).mkdir(parents=True, exist_ok=True)

        self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

        self._client = chromadb.PersistentClient(path=CHROMA_DIR)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def add_job(self, jd_text: str, metadata: Dict[str, Any], job_uuid: str) -> None:
        """
        Embed and store a job description in the vector store.

        Args:
            jd_text:   Full job description text.
            metadata:  Dict with company, title, url, etc.
            job_uuid:  The SHA-256 UUID used as the ChromaDB document ID.
        """
        self._ensure_initialized()

        # Stringify all metadata values (ChromaDB requirement)
        safe_meta = {k: str(v) for k, v in metadata.items()}

        self._collection.add(
            documents=[jd_text],
            metadatas=[safe_meta],
            ids=[job_uuid],
        )

    def check_semantic_duplicate(
        self,
        new_jd: str,
        threshold: float = SEMANTIC_DEDUP_THRESHOLD,
    ) -> bool:
        """
        Return True if a semantically similar JD already exists.

        ChromaDB returns cosine distance (0 = identical, 1 = orthogonal).
        We convert to similarity: similarity = 1 - distance.
        """
        self._ensure_initialized()

        if self._collection.count() == 0:
            return False

        results = self._collection.query(
            query_texts=[new_jd],
            n_results=1,
            include=["distances"],
        )

        distances = results.get("distances", [[]])[0]
        if not distances:
            return False

        similarity = 1.0 - distances[0]
        return similarity >= threshold

    def search_similar(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Return the top-N most similar job descriptions for a query string.
        Useful for RAG-based resume tailoring.
        """
        self._ensure_initialized()

        if self._collection.count() == 0:
            return []

        results = self._collection.query(
            query_texts=[query],
            n_results=min(n_results, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        output = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for doc, meta, dist in zip(docs, metas, dists):
            output.append({
                "text": doc,
                "metadata": meta,
                "similarity": round(1.0 - dist, 4),
            })

        return output

    def count(self) -> int:
        """Return total number of stored job descriptions."""
        self._ensure_initialized()
        return self._collection.count()
