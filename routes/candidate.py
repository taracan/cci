"""Candidate routes — dashboard, upload resume, view profile, find matching jobs."""
import json
import re
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
from role_titles import split_target_roles

candidate = Blueprint("candidate", __name__, url_prefix="/candidate")


def _load_parsed_resume(candidate_id):
    """Load parsed resume data for a candidate."""
    parsed_path = PARSED_RESUMES_DIR / f"{candidate_id}.json"
    if not parsed_path.exists():
        return None
    with open(parsed_path) as f:
        return json.load(f)


def _role_choice(cand):
    """The candidate's target roles and the one to score with.

    Roles come from the target-role field typed at registration, split on
    , ; | or "or" (see role_titles.split_target_roles). The selected role is
    ?role=... when it is one of them (case-insensitive), otherwise the first
    role typed. Returns (roles, selected_role); selected_role is "" if none.
    """
    roles = split_target_roles((cand or {}).get("target_role", ""))
    wanted = " ".join(request.args.get("role", "").split()).lower()
    selected = next((r for r in roles if r.lower() == wanted), roles[0] if roles else "")
    return roles, selected


def _load_all_jds():
    """Load all job descriptions."""
    jds_file = JDS_DIR / "jds.json"
    if not jds_file.exists():
        return []
    with open(jds_file) as f:
        return json.load(f)


def _tokenize_role(value):
    """Return searchable tokens for a role title or target-role string."""
    if not value:
        return set()
    return {token for token in re.findall(r"[A-Za-z0-9+#]+", value.lower()) if len(token) > 2}


def _role_suggestion_matches(target_role, jd):
    """True when a JD is a close role match for the candidate target_role."""
    target = (target_role or "").strip()
    if not target:
        return False
    jd_title = (jd.get("role_title") or "").strip()
    if not jd_title:
        return False
    target_tokens = _tokenize_role(target)
    jd_tokens = _tokenize_role(jd_title)
    skill_tokens = _tokenize_role(" ".join(jd.get("required_skills", []) + jd.get("preferred_skills", [])))

    if target.lower() in jd_title.lower() or jd_title.lower() in target.lower():
        return True
    if target_tokens and (target_tokens & jd_tokens or target_tokens & skill_tokens):
        return True
    return False


def store_resume_for_candidate(candidate_id, file_obj):
    """Parse and index a resume for a candidate after upload."""
    if not file_obj or not getattr(file_obj, "filename", None):
        return False, "Please select a resume file."

    suffix = Path(file_obj.filename).suffix.lower()
    if suffix not in (".pdf", ".txt"):
        return False, "Only PDF or TXT files accepted."

    file_bytes = file_obj.read()
    original_filename = file_obj.filename
    content_type = "application/pdf" if suffix == ".pdf" else "text/plain"
    s3_key = s3_storage.resume_key(candidate_id, suffix)

    try:
        s3_storage.upload_file(file_bytes, s3_key, content_type)
    except Exception as exc:
        return False, f"Could not store resume in S3: {exc}"

    try:
        from resume_parser import extract_text_from_bytes
        stored_bytes = s3_storage.download_file(s3_key)
        raw_text = extract_text_from_bytes(stored_bytes, original_filename)
        parsed = parse_resume_smart(raw_text)
    except Exception as exc:
        return False, f"Error parsing resume: {exc}"

    parsed_path = PARSED_RESUMES_DIR / f"{candidate_id}.json"
    with open(parsed_path, "w") as f:
        json.dump(parsed, f, indent=2)

    try:
        index_resume(candidate_id, parsed["raw_text"])
    except Exception:
        pass

    update_candidate(candidate_id, {
        "resume_uploaded": True,
        "resume_file": s3_key,
        "resume_uploaded_at": utc_now_iso(),
    })

    return True, {
        "parsed": parsed,
        "message": (
            f"Resume uploaded! Found {len(parsed['skills'])} skills, "
            f"{parsed['experience_years']} years experience, "
            f"education: {parsed['education']}."
        )
    }


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

    cid = get_current_user_id()
    file = request.files.get("resume")
    ok, result = store_resume_for_candidate(cid, file)
    if not ok:
        flash(result, "danger")
        return render_template("upload_resume.html")
    flash(result["message"], "success")
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
    return render_template(
        "profile.html", candidate=cand, parsed=parsed, saved=saved,
        roles=split_target_roles((cand or {}).get("target_role", "")),
        edit_roles=request.args.get("edit") == "roles",
        # Where "Save"/"Cancel" return to, e.g. Find Jobs (same-app paths only).
        roles_next=_local_path(request.args.get("next", "")),
    )


@candidate.route("/match-jobs")
@login_required
@candidate_required
def match_jobs():
    """Show resume-based matches or role-targeted suggestions when no resume exists."""
    cid = get_current_user_id()
    cand = get_candidate(cid)
    # Score for ONE target role at a time: the one picked in the dropdown
    # (?role=...), or the first role the candidate typed.
    roles, selected_role = _role_choice(cand)
    parsed = _load_parsed_resume(cid)
    all_jds = _load_all_jds()
    active_jds = [j for j in all_jds if j.get("is_active", True)]

    if not parsed:
        target_role = (cand or {}).get("target_role", "").strip()
        if not target_role:
            flash("Set your target role to browse relevant jobs.", "warning")
            return redirect(url_for("candidate.candidate_dashboard"))

        matches = []
        for jd in active_jds:
            if _role_suggestion_matches(target_role, jd):
                matches.append({
                    "jd": jd,
                    "result": {
                        "overall_score": 0,
                        "skill_score": 0,
                        "experience_score": 0,
                        "role_score": 0,
                        "matched_skills": [],
                        "missing_skills": [],
                    },
                    "points": description_points(jd.get("jd_text", "")),
                })

        facets = job_filters.build_facets(m["jd"] for m in matches)
        selection = job_filters.read_selection(request.args)
        shown = job_filters.filter_and_sort(matches, selection)

        chips = job_filters.active_chips(selection, facets)
        for chip in chips:
            chip["remove_url"] = _url_without(chip["param"], chip["value"])

        return render_template(
            "match_results.html", matches=shown, total=len(matches), parsed=None,
            candidate=cand, saved_ids=saved_jobs.saved_ids(cid),
            facets=facets, selection=selection, chips=chips,
            sort_options=job_filters.SORT_OPTIONS, role_suggestions=True,
            target_role=target_role,
            roles=roles, selected_role=selected_role,
            role_arg={"role": selected_role} if len(roles) > 1 else {},
            clear_url=url_for("candidate.match_jobs"),
        )

    matches = []
    for jd in active_jds:
        sem = semantic_score(jd.get("jd_text", ""), cid, jd.get("jd_id"))
        result = calculate_match(
            candidate_skills=parsed["skills"],
            candidate_exp=parsed["experience_years"],
            candidate_role=selected_role,
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

    facets = job_filters.build_facets(m["jd"] for m in matches)
    selection = job_filters.read_selection(request.args)
    shown = job_filters.filter_and_sort(matches, selection)

    chips = job_filters.active_chips(selection, facets)
    for chip in chips:
        chip["remove_url"] = _url_without(chip["param"], chip["value"])

    # Only put ?role= in links when there is a choice to remember.
    role_arg = {"role": selected_role} if len(roles) > 1 else {}
    # "Clear all" drops the filters but keeps the chosen sort and target role.
    keep = dict(role_arg)
    if selection["sort"] != "match":
        keep["sort"] = selection["sort"]

    return render_template(
        "match_results.html", matches=shown, total=len(matches), parsed=parsed,
        candidate=cand, saved_ids=saved_jobs.saved_ids(cid),
        facets=facets, selection=selection, chips=chips,
        sort_options=job_filters.SORT_OPTIONS, role_suggestions=False,
        roles=roles, selected_role=selected_role, role_arg=role_arg,
        clear_url=url_for("candidate.match_jobs", **keep),
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

    # Same role as the Find Jobs card that linked here (?role=...), so the
    # numbers agree; the first target role when none is given.
    roles, selected_role = _role_choice(cand)

    sem = semantic_score(
        target_jd.get("jd_text", ""),
        cid,
        target_jd.get("jd_id"),
    )
    result = calculate_match(
        candidate_skills=parsed["skills"],
        candidate_exp=parsed["experience_years"],
        candidate_role=selected_role,
        jd_required=target_jd["required_skills"],
        jd_preferred=target_jd.get("preferred_skills", []),
        jd_exp=target_jd["experience_years"],
        jd_title=target_jd["role_title"],
        semantic_score=sem,
    )
    return render_template(
        "match_detail.html", jd=target_jd, result=result, parsed=parsed,
        roles=roles, selected_role=selected_role,
        role_arg={"role": selected_role} if len(roles) > 1 else {},
    )


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


# Longest target-role text accepted from the profile form (all roles together).
TARGET_ROLE_MAX_LEN = 200


@candidate.route("/target-roles", methods=["POST"])
@login_required
@candidate_required
def update_target_roles():
    """Change the candidate's target role(s) from the Profile page.

    Accepts the same format as registration ("Data Analyst, ML Engineer"),
    tidies it (split on , ; | or "or", blanks/duplicates dropped) and saves it
    as "Role A, Role B". An empty value clears the target role. Matching picks
    up the change immediately; the first role becomes the Find Jobs default.
    Returns to ?next (a path inside this app) or to the Profile page.
    """
    back_to = _local_path(request.form.get("next", ""))
    next_url = back_to or url_for("candidate.profile") + "#target-roles"

    raw = request.form.get("target_role", "")
    if len(raw) > TARGET_ROLE_MAX_LEN:
        flash(f"Target roles are too long (max {TARGET_ROLE_MAX_LEN} characters).", "danger")
        keep = {"next": back_to} if back_to else {}
        return redirect(url_for("candidate.profile", edit="roles", **keep) + "#target-roles")

    roles = split_target_roles(raw)
    update_candidate(get_current_user_id(), {"target_role": ", ".join(roles)})
    if roles:
        flash(f"Target role{'s' if len(roles) > 1 else ''} updated: {', '.join(roles)}.", "success")
    else:
        flash("Target role cleared. Add one any time to improve your job matches.", "info")
    return redirect(next_url)


def _local_path(url: str) -> str:
    """Return url if it is a path inside this app ("/..."), else "" (blocks open redirects)."""
    return url if url.startswith("/") and not url.startswith("//") else ""


def _wants_json() -> bool:
    """True when the request came from our fetch() call rather than a form."""
    return request.headers.get("X-Requested-With") == "fetch"