"""Semantic vector matching using ChromaDB + all-MiniLM-L6-v2.

Complements the keyword-based matching_engine by adding semantic similarity
between a candidate's resume text and a job description. Both resumes and JDs
are embedded and stored in persistent ChromaDB collections.

The model and client are loaded lazily so importing this module (e.g. during
tests that don't use semantic search) stays cheap.
"""
from __future__ import annotations

import logging
from typing import Optional

from config import DATA_DIR

logger = logging.getLogger(__name__)

# Persistent ChromaDB store lives alongside the other portal data.
CHROMA_DIR = DATA_DIR / "chroma"
RESUME_COLLECTION = "resumes"
JD_COLLECTION = "jds"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Lazily initialised singletons.
_model = None
_client = None
_resume_collection = None
_jd_collection = None


def _get_model():
    """Load (once) and return the sentence-transformer embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def _get_client():
    """Return the persistent ChromaDB client, creating it if needed."""
    global _client
    if _client is None:
        import chromadb
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def _get_resume_collection():
    """Return the persistent ChromaDB resume collection, creating it if needed."""
    global _resume_collection
    if _resume_collection is None:
        _resume_collection = _get_client().get_or_create_collection(name=RESUME_COLLECTION)
    return _resume_collection


def _get_jd_collection():
    """Return the persistent ChromaDB JD collection, creating it if needed."""
    global _jd_collection
    if _jd_collection is None:
        _jd_collection = _get_client().get_or_create_collection(name=JD_COLLECTION)
    return _jd_collection


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
    collection = _get_resume_collection()
    collection.upsert(
        ids=[candidate_id],
        embeddings=[embed_text(resume_text)],
        documents=[resume_text],
        metadatas=[{"candidate_id": candidate_id}],
    )


def index_jd(jd_id: str, jd_text: str) -> None:
    """Embed a JD and upsert it into the vector store.

    Using the jd_id as the document id means re-uploading a JD overwrites
    the previous vector instead of creating duplicates.

    Args:
        jd_id: Unique JD id (used as the ChromaDB document id).
        jd_text: The raw JD text to embed and store.
    """
    if not jd_text.strip():
        return
    try:
        collection = _get_jd_collection()
        collection.upsert(
            ids=[jd_id],
            embeddings=[embed_text(jd_text)],
            documents=[jd_text],
            metadatas=[{"jd_id": jd_id}],
        )
    except Exception as e:
        logger.warning(f"Failed to index JD {jd_id}: {e}")


def delete_jd(jd_id: str) -> None:
    """Delete a JD embedding from the vector store.

    Args:
        jd_id: The JD id to delete.
    """
    try:
        collection = _get_jd_collection()
        collection.delete(ids=[jd_id])
    except Exception as e:
        logger.warning(f"Failed to delete JD {jd_id} from vector store: {e}")


def semantic_score(jd_text: str, candidate_id: str, jd_id: Optional[str] = None) -> Optional[float]:
    """Return a 0–100 semantic similarity score for one candidate vs a JD.

    If jd_id is provided, uses the pre-stored JD embedding. Otherwise, embeds
    the JD text on-demand (backwards compatibility).

    Args:
        jd_text: The job description text (query).
        candidate_id: The candidate whose stored resume vector to compare.
        jd_id: Optional JD id to use pre-stored embedding. If None, embeds on-demand.

    Returns:
        A 0–100 similarity score, or None if the candidate has no stored resume
        or the JD text is empty.
    """
    if not jd_text.strip():
        return None
    resume_collection = _get_resume_collection()
    if resume_collection.count() == 0:
        return None

    if jd_id:
        try:
            jd_collection = _get_jd_collection()
            jd_results = jd_collection.query(
                query_texts=[jd_text],
                n_results=1,
                include=["distances"],
                where={"jd_id": jd_id},
            )
            if jd_results["ids"][0]:
                jd_embedding = jd_results["embeddings"][0][0]
            else:
                logger.debug(f"JD {jd_id} not found in vector store, falling back to on-demand embedding")
                jd_embedding = embed_text(jd_text)
        except Exception as e:
            logger.debug(f"Error retrieving JD embedding: {e}, falling back to on-demand")
            jd_embedding = embed_text(jd_text)
    else:
        jd_embedding = embed_text(jd_text)

    results = resume_collection.query(
        query_embeddings=[jd_embedding],
        n_results=resume_collection.count(),
        include=["distances"],
    )
    ids = results["ids"][0]
    if candidate_id not in ids:
        return None

    distance = results["distances"][0][ids.index(candidate_id)]
    similarity = 1 / (1 + distance)
    return round(similarity * 100, 1)
