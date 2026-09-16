"""One-off script to index already-parsed resumes into the vector store.

Run once after adding semantic matching so resumes uploaded BEFORE this
feature existed get embedded too:

    python3 backfill_vectors.py
"""
import json

from config import PARSED_RESUMES_DIR
from vector_matcher import index_resume


def main() -> None:
    """Index every parsed resume JSON found on disk into ChromaDB."""
    files = list(PARSED_RESUMES_DIR.glob("*.json"))
    if not files:
        print("No parsed resumes found to backfill.")
        return

    count = 0
    for path in files:
        candidate_id = path.stem
        with open(path) as f:
            parsed = json.load(f)
        raw_text = parsed.get("raw_text", "")
        if raw_text.strip():
            index_resume(candidate_id, raw_text)
            count += 1
            print(f"  indexed {candidate_id}")

    print(f"\nBackfill complete: {count} resume(s) indexed.")


if __name__ == "__main__":
    main()
