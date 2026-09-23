"""Candidate routes — dashboard, upload resume, view profile, find matching jobs."""
import json
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from auth import login_required, candidate_required, get_candidate, update_candidate, get_current_user_id
from config import PARSED_RESUMES_DIR, JDS_DIR
from matching_engine import calculate_match
from vector_matcher import index_resume, semantic_score
from llm.llm_service import parse_resume_smart
from storage import s3 as s3_storage

candidate = Blueprint("candidate", __name__, url_prefix="/candidate")


def _load_parsed_resume(candidate_id):
    """Load parsed resume data for a candidate."""
    parsed_path = PARSED_RESUMES_DIR / f"{candidate_id}.json"
    if not parsed_path.exists():
        return None
    with open(parsed_path) as f:
        return json.load(f)


def _load_all_jds():
    """Load all job descriptions."""
    jds_file = JDS_DIR / "jds.json"
    if not jds_file.exists():
        return []
    with open(jds_file) as f:
        return json.load(f)


@candidate.route("/dashboard")
@login_required
@candidate_required
def candidate_dashboard():
    """Candidate main dashboard — shows profile and parsed resume info."""
    cid = get_current_user_id()
    cand = get_candidate(cid)
    parsed = _load_parsed_resume(cid)
    return render_template("candidate_dashboard.html", candidate=cand, parsed=parsed)


@candidate.route("/upload-resume", methods=["GET", "POST"])
@login_required
@candidate_required
def upload_resume():
    """Upload (or replace) a resume (PDF or TXT).

    Each upload overwrites the same deterministic S3 key for this candidate,
    so there is never more than one live resume object per candidate.
    The parsed-resume JSON and the ChromaDB embedding are replaced in the
    same request, so stale data cannot be matched against after an update.
    """
    if request.method == "GET":
        return render_template("upload_resume.html")

    file = request.files.get("resume")
    if not file or not file.filename:
        flash("Please select a file.", "danger")
        return render_template("upload_resume.html")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".pdf", ".txt"):
        flash("Only PDF or TXT files accepted.", "danger")
        return render_template("upload_resume.html")

    cid = get_current_user_id()

    # Read file bytes once — used for both S3 upload and text extraction.
    file_bytes = file.read()
    original_filename = file.filename
    content_type = "application/pdf" if suffix == ".pdf" else "text/plain"

    # Build the deterministic key — same candidate always writes to the same
    # S3 object, so a re-upload silently overwrites the previous document.
    s3_key = s3_storage.resume_key(cid, suffix)

    # Upload to S3 (private, overwrites any previous object at this key).
    try:
        s3_storage.upload_file(file_bytes, s3_key, content_type)
    except Exception as e:
        flash(f"Could not store resume in S3: {e}", "danger")
        return render_template("upload_resume.html")

    # Retrieve from S3 and extract text (validates the upload round-trip).
    try:
        from resume_parser import extract_text_from_bytes
        stored_bytes = s3_storage.download_file(s3_key)
        raw_text = extract_text_from_bytes(stored_bytes, original_filename)
        parsed = parse_resume_smart(raw_text)
    except Exception as e:
        flash(f"Error parsing resume: {e}", "danger")
        return render_template("upload_resume.html")

    # Replace parsed-resume JSON on disk (overwrites previous record).
    parsed_path = PARSED_RESUMES_DIR / f"{cid}.json"
    with open(parsed_path, "w") as f:
        json.dump(parsed, f, indent=2)

    # Upsert resume embedding — ChromaDB uses candidate_id as the document id,
    # so this always replaces the previous vector; no stale embeddings remain.
    try:
        index_resume(cid, parsed["raw_text"])
    except Exception as e:
        # Semantic indexing is best-effort; keyword matching still works.
        flash(f"Note: semantic indexing skipped ({e}).", "warning")

    # Store the deterministic S3 key in the candidate record.
    update_candidate(cid, {"resume_uploaded": True, "resume_file": s3_key})

    flash(
        f"Resume uploaded! Found {len(parsed['skills'])} skills, "
        f"{parsed['experience_years']} years experience, "
        f"education: {parsed['education']}.",
        "success",
    )
    return redirect(url_for("candidate.candidate_dashboard"))


@candidate.route("/profile")
@login_required
@candidate_required
def profile():
    """View full candidate profile with all parsed data."""
    cid = get_current_user_id()
    cand = get_candidate(cid)
    parsed = _load_parsed_resume(cid)
    return render_template("profile.html", candidate=cand, parsed=parsed)


@candidate.route("/match-jobs")
@login_required
@candidate_required
def match_jobs():
    """Find and rank matching jobs based on resume vs all active JDs."""
    cid = get_current_user_id()
    parsed = _load_parsed_resume(cid)

    if not parsed:
        flash("Upload your resume first to see job matches.", "warning")
        return redirect(url_for("candidate.upload_resume"))

    cand = get_candidate(cid)
    all_jds = _load_all_jds()
    active_jds = [j for j in all_jds if j.get("is_active", True)]

    matches = []
    for jd in active_jds:
        sem = semantic_score(jd.get("jd_text", ""), cid)
        result = calculate_match(
            candidate_skills=parsed["skills"],
            candidate_exp=parsed["experience_years"],
            candidate_role=cand.get("target_role", ""),
            jd_required=jd["required_skills"],
            jd_preferred=jd.get("preferred_skills", []),
            jd_exp=jd["experience_years"],
            jd_title=jd["role_title"],
            semantic_score=sem,
        )
        matches.append({"jd": jd, "result": result})

    matches.sort(key=lambda x: x["result"]["overall_score"], reverse=True)
    return render_template("match_results.html", matches=matches, parsed=parsed, candidate=cand)


@candidate.route("/match-jobs/<jd_id>")
@login_required
@candidate_required
def match_detail(jd_id):
    """Detailed match view for a single JD."""
    cid = get_current_user_id()
    parsed = _load_parsed_resume(cid)
    if not parsed:
        flash("Upload your resume first.", "warning")
        return redirect(url_for("candidate.upload_resume"))

    cand = get_candidate(cid)
    all_jds = _load_all_jds()
    target_jd = next((j for j in all_jds if j["jd_id"] == jd_id), None)

    if not target_jd:
        flash("Job description not found.", "danger")
        return redirect(url_for("candidate.match_jobs"))

    sem = semantic_score(target_jd.get("jd_text", ""), cid)
    result = calculate_match(
        candidate_skills=parsed["skills"],
        candidate_exp=parsed["experience_years"],
        candidate_role=cand.get("target_role", ""),
        jd_required=target_jd["required_skills"],
        jd_preferred=target_jd.get("preferred_skills", []),
        jd_exp=target_jd["experience_years"],
        jd_title=target_jd["role_title"],
        semantic_score=sem,
    )
    return render_template("match_detail.html", jd=target_jd, result=result, parsed=parsed)
