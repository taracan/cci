"""One-off script to index already-parsed resumes and JDs into the vector store.

Run once after adding semantic matching so resumes/JDs uploaded BEFORE this
feature existed get embedded too:

    python3 backfill_vectors.py --resumes    # backfill resumes only
    python3 backfill_vectors.py --jds        # backfill JDs only
    python3 backfill_vectors.py               # backfill both (default)
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any

from config import PARSED_RESUMES_DIR, JDS_DIR
from vector_matcher import index_resume, index_jd


def backfill_resumes() -> None:
    """Index every parsed resume JSON found on disk into ChromaDB.

    Iterates through all JSON files in PARSED_RESUMES_DIR, extracts the
    raw_text field, and indexes each resume into the resume collection.

    Returns:
        None

    Prints:
        Progress messages for each indexed resume
        Summary of number of resumes indexed
    """
    files: list[Path] = list(PARSED_RESUMES_DIR.glob("*.json"))
    if not files:
        print("No parsed resumes found to backfill.")
        return

    count: int = 0
    for path in files:
        try:
            candidate_id: str = path.stem
            with open(path, encoding="utf-8") as f:
                parsed: dict[str, Any] = json.load(f)
            raw_text: str = parsed.get("raw_text", "")
            if raw_text.strip():
                index_resume(candidate_id, raw_text)
                count += 1
                print(f"  ✓ indexed resume {candidate_id}")
        except (json.JSONDecodeError, IOError, Exception) as e:
            print(f"  ✗ failed to index resume {path.stem}: {e}", file=sys.stderr)
            continue

    print(f"Resumes backfilled: {count} indexed.")


def backfill_jds() -> None:
    """Index every JD from jds.json into ChromaDB.

    Loads JDs from JDS_DIR/jds.json, extracts jd_id and jd_text fields,
    and indexes each JD into the JD collection.

    Returns:
        None

    Prints:
        Progress messages for each indexed JD
        Summary of number of JDs indexed
    """
    jds_file: Path = JDS_DIR / "jds.json"
    if not jds_file.exists():
        print("No jds.json file found to backfill.")
        return

    try:
        with open(jds_file, encoding="utf-8") as f:
            jds: list[dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading jds.json: {e}", file=sys.stderr)
        return

    if not jds:
        print("No JDs found to backfill.")
        return

    count: int = 0
    for jd in jds:
        try:
            jd_id: str | None = jd.get("jd_id")
            jd_text: str = jd.get("jd_text", "")
            # Deactivated JDs are kept out of the vector store (see delete route).
            if jd_id and jd_text.strip() and jd.get("is_active", True):
                index_jd(jd_id, jd_text)
                count += 1
                print(f"  ✓ indexed JD {jd_id}")
        except Exception as e:
            jd_id = jd.get("jd_id", "unknown")
            print(f"  ✗ failed to index JD {jd_id}: {e}", file=sys.stderr)
            continue

    print(f"JDs backfilled: {count} indexed.")


def main() -> None:
    """Index resumes and/or JDs into ChromaDB based on command-line arguments.

    Parses command-line arguments to determine whether to backfill resumes,
    JDs, or both. By default (no args), backfills both.

    Returns:
        None

    Exit codes:
        0: Success
        1: Error during processing
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Backfill vector embeddings for resumes and/or JDs"
    )
    parser.add_argument("--resumes", action="store_true", help="Backfill resumes only")
    parser.add_argument("--jds", action="store_true", help="Backfill JDs only")
    args: argparse.Namespace = parser.parse_args()

    backfill_resumes_only: bool = args.resumes and not args.jds
    backfill_jds_only: bool = args.jds and not args.resumes
    backfill_both: bool = not args.resumes and not args.jds

    try:
        if backfill_resumes_only or backfill_both:
            print("Backfilling resumes...")
            backfill_resumes()

        if backfill_jds_only or backfill_both:
            print("Backfilling JDs...")
            backfill_jds()

        print("\n✓ Backfill complete.")
    except Exception as e:
        print(f"\n✗ Backfill failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
