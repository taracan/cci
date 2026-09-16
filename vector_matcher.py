"""Semantic vector matching using ChromaDB + all-MiniLM-L6-v2.

Complements the keyword-based matching_engine by adding semantic similarity
between a candidate's resume text and a job description. Resumes are embedded
and stored in a persistent ChromaDB collection; a JD is embedded on demand and
compared to stored resumes.

The model and client are loaded lazily so importing this module (e.g. during
tests that don't use semantic search) stays cheap.
"""
from __future__ import annotations

from typing import Optional

from config import DATA_DIR

# Persistent ChromaDB store lives alongside the other portal data.
CHROMA_DIR = DATA_DIR / "chroma"
RESUME_COLLECTION = "resumes"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Lazily initialised singletons.
_model = None
_client = None
_collection = None


def _get_model():
    """Load (once) and return the sentence-transformer embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def _get_collection():
    """Return the persistent ChromaDB resume collection, creating it if needed."""
    global _client, _collection
    if _collection is None:
        import chromadb
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(name=RESUME_COLLECTION)
    return _collection


def embed_text(text: str) -> list[float]:
    """Embed a piece of text into a dense vector.

    Args:
        text: The text to embed.

    Returns:
        The embedding as a list of floats.
    """
    return _get_model().encode(text).tolist()


def index_resume(candidate_id: str, resume_text: str) -> None:
    """Embed a resume and upsert it into the vector store.

    Using the candidate_id as the document id means re-uploading a resume
    overwrites the previous vector instead of creating duplicates.

    Args:
        candidate_id: Unique candidate id (used as the ChromaDB document id).
        resume_text: The raw resume text to embed and store.
    """
    if not resume_text.strip():
        return
    collection = _get_collection()
    collection.upsert(
        ids=[candidate_id],
        embeddings=[embed_text(resume_text)],
        documents=[resume_text],
        metadatas=[{"candidate_id": candidate_id}],
    )


def semantic_score(jd_text: str, candidate_id: str) -> Optional[float]:
    """Return a 0–100 semantic similarity score for one candidate vs a JD.

    Args:
        jd_text: The job description text (query).
        candidate_id: The candidate whose stored resume vector to compare.

    Returns:
        A 0–100 similarity score, or None if the candidate has no stored resume
        or the JD text is empty.
    """
    if not jd_text.strip():
        return None
    collection = _get_collection()
    if collection.count() == 0:
        return None

    results = collection.query(
        query_embeddings=[embed_text(jd_text)],
        n_results=collection.count(),
        include=["distances"],
    )
    ids = results["ids"][0]
    if candidate_id not in ids:
        return None

    distance = results["distances"][0][ids.index(candidate_id)]
    similarity = 1 / (1 + distance)          # map distance → (0, 1]
    return round(similarity * 100, 1)
