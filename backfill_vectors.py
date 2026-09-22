"""One-off script to index already-parsed resumes and JDs into the vector store.

Run once after adding semantic matching so resumes/JDs uploaded BEFORE this
feature existed get embedded too:

    python3 backfill_vectors.py --resumes    # backfill resumes only
    python3 backfill_vectors.py --jds        # backfill JDs only
    python3 backfill_vectors.py               # backfill both (default)
"""
import argparse
import json

from config import PARSED_RESUMES_DIR, JDS_DIR
from vector_matcher import index_resume, index_jd


def backfill_resumes() -> None:
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
            print(f"  indexed resume {candidate_id}")

    print(f"Resumes backfilled: {count} indexed.")


def backfill_jds() -> None:
    """Index every JD from jds.json into ChromaDB."""
    jds_file = JDS_DIR / "jds.json"
    if not jds_file.exists():
        print("No jds.json file found to backfill.")
        return

    with open(jds_file) as f:
        jds = json.load(f)

    if not jds:
        print("No JDs found to backfill.")
        return

    count = 0
    for jd in jds:
        jd_id = jd.get("jd_id")
        jd_text = jd.get("jd_text", "")
        if jd_id and jd_text.strip():
            index_jd(jd_id, jd_text)
            count += 1
            print(f"  indexed JD {jd_id}")

    print(f"JDs backfilled: {count} indexed.")


def main() -> None:
    """Index resumes and/or JDs into ChromaDB based on arguments."""
    parser = argparse.ArgumentParser(description="Backfill vector embeddings for resumes and/or JDs")
    parser.add_argument("--resumes", action="store_true", help="Backfill resumes only")
    parser.add_argument("--jds", action="store_true", help="Backfill JDs only")
    args = parser.parse_args()

    backfill_resumes_only = args.resumes and not args.jds
    backfill_jds_only = args.jds and not args.resumes
    backfill_both = not args.resumes and not args.jds

    if backfill_resumes_only or backfill_both:
        print("Backfilling resumes...")
        backfill_resumes()

    if backfill_jds_only or backfill_both:
        print("Backfilling JDs...")
        backfill_jds()

    print("\nBackfill complete.")


if __name__ == "__main__":
    main()
