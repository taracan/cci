"""Candidate routes — dashboard, upload resume, view profile, find matching jobs."""
import json
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from auth import login_required, candidate_required, get_candidate, update_candidate, get_current_user_id
from config import PARSED_RESUMES_DIR, JDS_DIR
from matching_engine import calculate_match
from vector_matcher import index_resume, semantic_score
from llm.llm_service import parse_resume_smart
from storage import s3 as s3_storage
from jd_text import description_points
import saved_jobs
import job_filters
from timefmt import utc_now_iso

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
    update_candidate(cid, {"resume_uploaded": True, "resume_file": s3_key,
                           "resume_uploaded_at": utc_now_iso()})

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

    # Saved jobs, newest first. Skip any JD that no longer exists at all;
    # deactivated JDs stay listed (the template marks them as closed).
    jds_by_id = {j["jd_id"]: j for j in _load_all_jds()}
    saved = [
        {"jd": jds_by_id[s["jd_id"]], "saved_at": s.get("saved_at", "")}
        for s in saved_jobs.get_saved(cid)
        if s["jd_id"] in jds_by_id
    ]
    return render_template("profile.html", candidate=cand, parsed=parsed, saved=saved)


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
        sem = semantic_score(jd.get("jd_text", ""), cid, jd.get("jd_id"))
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
        matches.append({
            "jd": jd,
            "result": result,
            "points": description_points(jd.get("jd_text", "")),
        })

    # Filters: options come from ALL active jobs (so counts don't shrink as you
    # filter); the selection comes from the URL (?loc=..&exp=..&sal=..&sort=..).
    facets = job_filters.build_facets(m["jd"] for m in matches)
    selection = job_filters.read_selection(request.args)
    shown = job_filters.filter_and_sort(matches, selection)

    chips = job_filters.active_chips(selection, facets)
    for chip in chips:
        chip["remove_url"] = _url_without(chip["param"], chip["value"])

    return render_template(
        "match_results.html", matches=shown, total=len(matches), parsed=parsed,
        candidate=cand, saved_ids=saved_jobs.saved_ids(cid),
        facets=facets, selection=selection, chips=chips,
        sort_options=job_filters.SORT_OPTIONS,
    )


def _url_without(param: str, value: str) -> str:
    """Current Find Jobs URL with one filter value removed (for the chip's ×)."""
    args = request.args.copy()
    remaining = [v for v in args.getlist(param) if v != value]
    args.setlist(param, remaining)
    return url_for("candidate.match_jobs", **args.to_dict(flat=False))


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

    sem = semantic_score(
        target_jd.get("jd_text", ""),
        cid,
        target_jd.get("jd_id"),
    )
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


@candidate.route("/jobs/<jd_id>/save", methods=["POST"])
@login_required
@candidate_required
def toggle_save_job(jd_id):
    """Save or unsave a job for the current candidate.

    Called two ways:
      - from JavaScript (fetch) on the matches page → returns JSON, no reload
      - from a plain HTML form (e.g. "Remove" on the profile) → redirects back
    """
    if not any(j["jd_id"] == jd_id for j in _load_all_jds()):
        if _wants_json():
            return jsonify({"error": "Job not found"}), 404
        flash("Job not found.", "danger")
        return redirect(url_for("candidate.match_jobs"))

    cid = get_current_user_id()
    now_saved = saved_jobs.toggle_saved(cid, jd_id)

    if _wants_json():
        return jsonify({"saved": now_saved, "count": len(saved_jobs.get_saved(cid))})

    flash("Job saved." if now_saved else "Job removed from saved jobs.", "success")
    # Only follow redirects back into our own app.
    next_url = request.form.get("next", "")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = url_for("candidate.match_jobs")
    return redirect(next_url)


def _wants_json() -> bool:
    """True when the request came from our fetch() call rather than a form."""
    return request.headers.get("X-Requested-With") == "fetch"
