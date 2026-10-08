"""Semantic vector matching using ChromaDB + all-MiniLM-L6-v2.

Complements the keyword-based matching_engine by adding semantic similarity
between a candidate's resume text and a job description. Both resumes and JDs
are embedded and stored in persistent ChromaDB collections.

The model and client are loaded lazily so importing this module (e.g. during
tests that don't use semantic search) stays cheap.

Docs:
  PR changes:   https://claude.ai/artifact/YPTm3fio8tZedHKQaiQ6Du
  File summary: https://claude.ai/artifact/SGF5LFF67nSCBF2BnNJB8r
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Any

import chromadb
from chromadb.api.models.Collection import Collection
from sentence_transformers import SentenceTransformer

from config import DATA_DIR

logger = logging.getLogger(__name__)

# Persistent ChromaDB store lives alongside the other portal data.
CHROMA_DIR: Path = DATA_DIR / "chroma"
RESUME_COLLECTION: str = "resumes"
JD_COLLECTION: str = "jds"
EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"

# Lazily initialised singletons.
_model: Optional[SentenceTransformer] = None
_client: Optional[chromadb.PersistentClient] = None
_resume_collection: Optional[Collection] = None
_jd_collection: Optional[Collection] = None


def _get_model() -> SentenceTransformer:
    """Load (once) and return the sentence-transformer embedding model.

    Returns:
        SentenceTransformer: Cached embedding model instance.
    """
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def _get_client() -> chromadb.PersistentClient:
    """Return the persistent ChromaDB client, creating it if needed.

    Returns:
        chromadb.PersistentClient: Cached Chroma database client instance.
    """
    global _client
    if _client is None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def _get_resume_collection() -> Collection:
    """Return the persistent ChromaDB resume collection, creating it if needed.

    Returns:
        Collection: Cached resume embeddings collection.
    """
    global _resume_collection
    if _resume_collection is None:
        _resume_collection = _get_client().get_or_create_collection(name=RESUME_COLLECTION)
    return _resume_collection


def _get_jd_collection() -> Collection:
    """Return the persistent ChromaDB JD collection, creating it if needed.

    Returns:
        Collection: Cached JD embeddings collection.
    """
    global _jd_collection
    if _jd_collection is None:
        _jd_collection = _get_client().get_or_create_collection(name=JD_COLLECTION)
    return _jd_collection


def embed_text(text: str) -> list[float]:
    """Embed a piece of text into a dense vector.

    Args:
        text: The text to embed.

    Returns:
        list[float]: The embedding as a list of floats (384-dimensional).

    Raises:
        ValueError: If text is empty or None.
    """
    if not text or not text.strip():
        raise ValueError("Text to embed cannot be empty")
    return _get_model().encode(text).tolist()


def index_resume(candidate_id: str, resume_text: str) -> None:
    """Embed a resume and upsert it into the vector store.

    Using the candidate_id as the document id means re-uploading a resume
    overwrites the previous vector instead of creating duplicates.

    Args:
        candidate_id: Unique candidate id (used as the ChromaDB document id).
        resume_text: The raw resume text to embed and store.

    Returns:
        None

    Raises:
        Exception: If ChromaDB upsert operation fails.
    """
    if not resume_text.strip():
        return
    try:
        collection: Collection = _get_resume_collection()
        embedding: list[float] = embed_text(resume_text)
        collection.upsert(
            ids=[candidate_id],
            embeddings=[embedding],
            documents=[resume_text],
            metadatas=[{"candidate_id": candidate_id}],
        )
        logger.debug(f"Indexed resume for candidate {candidate_id}")
    except Exception as e:
        logger.error(f"Failed to index resume {candidate_id}: {e}", exc_info=True)
        raise


def index_jd(jd_id: str, jd_text: str) -> None:
    """Embed a JD and upsert it into the vector store.

    Using the jd_id as the document id means re-uploading a JD overwrites
    the previous vector instead of creating duplicates.

    Args:
        jd_id: Unique JD id (used as the ChromaDB document id).
        jd_text: The raw JD text to embed and store.

    Returns:
        None

    Note:
        If text is empty or embedding fails, logs a warning and returns gracefully
        to maintain backwards compatibility.
    """
    if not jd_text.strip():
        return
    try:
        collection: Collection = _get_jd_collection()
        embedding: list[float] = embed_text(jd_text)
        collection.upsert(
            ids=[jd_id],
            embeddings=[embedding],
            documents=[jd_text],
            metadatas=[{"jd_id": jd_id}],
        )
        logger.debug(f"Indexed JD {jd_id}")
    except Exception as e:
        logger.warning(f"Failed to index JD {jd_id}: {e}")


def delete_jd(jd_id: str) -> None:
    """Delete a JD embedding from the vector store.

    Args:
        jd_id: The JD id to delete.

    Returns:
        None

    Note:
        If deletion fails, logs a warning and continues gracefully.
    """
    try:
        collection: Collection = _get_jd_collection()
        collection.delete(ids=[jd_id])
        logger.debug(f"Deleted JD {jd_id} from vector store")
    except Exception as e:
        logger.warning(f"Failed to delete JD {jd_id} from vector store: {e}")


def semantic_score(
    jd_text: str,
    candidate_id: str,
    jd_id: Optional[str] = None
) -> Optional[float]:
    """Return a 0–100 semantic similarity score for one candidate vs a JD.

    If jd_id is provided, uses the pre-stored JD embedding. Otherwise, embeds
    the JD text on-demand (backwards compatibility).

    Args:
        jd_text: The job description text (query).
        candidate_id: The candidate whose stored resume vector to compare.
        jd_id: Optional JD id to use pre-stored embedding. If None, embeds on-demand.

    Returns:
        Optional[float]: A 0–100 similarity score, or None if the candidate has
                        no stored resume or the JD text is empty.

    Algorithm:
        1. Compute/retrieve JD embedding (from Chroma if jd_id provided, else on-demand)
        2. Query resume collection for candidate's resume embedding
        3. Calculate Euclidean distance between embeddings
        4. Convert distance to similarity score: similarity = 1 / (1 + distance)
        5. Scale to 0-100 range

    Raises:
        ValueError: If jd_text is empty or None.
    """
    if not jd_text.strip():
        return None

    resume_collection: Collection = _get_resume_collection()
    if resume_collection.count() == 0:
        return None

    jd_embedding: list[float]
    if jd_id:
        try:
            jd_collection: Collection = _get_jd_collection()
            # Retrieve pre-stored JD embedding from vector store
            jd_results: dict[str, Any] = jd_collection.get(
                ids=[jd_id],
                include=["embeddings"],
            )
            # Chroma returns embeddings as a numpy array, so check length
            # explicitly — `if array:` raises on multi-element arrays.
            stored: Any = jd_results.get("embeddings")
            if stored is not None and len(stored) > 0:
                jd_embedding = [float(x) for x in stored[0]]
                logger.debug(f"Using pre-indexed JD embedding for {jd_id}")
            else:
                logger.debug(
                    f"JD {jd_id} not found in vector store, falling back to on-demand embedding"
                )
                jd_embedding = embed_text(jd_text)
        except Exception as e:
            logger.debug(f"Error retrieving JD embedding for {jd_id}: {e}, falling back to on-demand")
            jd_embedding = embed_text(jd_text)
    else:
        jd_embedding = embed_text(jd_text)

    results: dict[str, Any] = resume_collection.query(
        query_embeddings=[jd_embedding],
        n_results=resume_collection.count(),
        include=["distances"],
    )

    ids: list[str] = results["ids"][0]
    if candidate_id not in ids:
        return None

    distance: float = results["distances"][0][ids.index(candidate_id)]
    similarity: float = 1 / (1 + distance)
    return round(similarity * 100, 1)
