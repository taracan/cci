"""Admin routes — dashboard, upload JD, manage JDs, view ranked candidates."""
import json
import uuid
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from auth import login_required, admin_required, get_all_candidates, get_current_user_id, load_json, save_json, ADMINS_FILE
from config import JDS_DIR, PARSED_RESUMES_DIR
from matching_engine import calculate_match
from models import JobDescription
from vector_matcher import semantic_score
from pipeline.matcher import two_stage_match
from observability.logging_config import get_logger
from storage import s3 as s3_storage
from resume_parser import extract_text_from_bytes
from salary import parse_salary_form
from timefmt import utc_now_iso

# MIME types accepted for JD file uploads
_JD_ALLOWED_SUFFIXES = {".pdf", ".docx"}
_JD_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

admin = Blueprint("admin", __name__, url_prefix="/admin")

_audit = get_logger("admin.action")

JDS_FILE = JDS_DIR / "jds.json"


def _load_jds():
    """Load all JDs from file."""
    return load_json(JDS_FILE)


def _save_jds(jds):
    """Save JDs to file."""
    save_json(JDS_FILE, jds)


def _mark_updated(jd):
    """Record when and by whom a JD was last changed."""
    jd["updated_at"] = utc_now_iso()
    jd["updated_by"] = get_current_user_id()


def _mark_reposted(jd):
    """Reactivation counts as a fresh posting.

    The posted time becomes "now" and the update is cleared, so candidates see
    "Posted just now". The original posting time is kept once in
    first_posted_at, and reposted_by records which admin reactivated it.
    """
    if not jd.get("first_posted_at"):
        jd["first_posted_at"] = jd.get("created_at")
    jd["created_at"] = utc_now_iso()
    jd["reposted_by"] = get_current_user_id()
    jd["updated_at"] = None
    jd["updated_by"] = None


def _admin_names():
    """{admin_id: name} for showing who created/updated a JD."""
    return {a["admin_id"]: a["name"] for a in load_json(ADMINS_FILE)}


@admin.route("/dashboard")
@login_required
@admin_required
def admin_dashboard():
    """Admin main dashboard — shows JDs and candidates overview."""
    jds = _load_jds()
    candidates = get_all_candidates()
    active_count = len([j for j in jds if j.get("is_active", True)])
    resume_count = len([c for c in candidates if c.get("resume_uploaded", False)])
    return render_template(
        "admin_dashboard.html",
        jds=jds,
        candidates=candidates,
        active_count=active_count,
        resume_count=resume_count,
    )


@admin.route("/upload-jd", methods=["GET", "POST"])
@login_required
@admin_required
def upload_jd():
    """Upload a new Job Description (typed text OR PDF/DOCX file)."""
    if request.method == "GET":
        return render_template("upload_jd.html")

    role_title = request.form.get("role_title", "").strip()
    company = request.form.get("company", "").strip()
    required_skills_raw = request.form.get("required_skills", "")
    preferred_skills_raw = request.form.get("preferred_skills", "")
    experience_years = request.form.get("experience_years", "0")
    jd_text = request.form.get("jd_text", "").strip()
    location = request.form.get("location", "Remote").strip()

    required_skills = [s.strip() for s in required_skills_raw.split(",") if s.strip()]
    preferred_skills = [s.strip() for s in preferred_skills_raw.split(",") if s.strip()]

    # --- Validate structural fields (always required) ---
    if not role_title or not company or not required_skills:
        flash("Please fill all required fields (Role, Company, Required Skills).", "danger")
        return render_template("upload_jd.html")

    # --- Salary (optional) ---
    salary_fields, salary_error = parse_salary_form(request.form)
    if salary_error:
        flash(salary_error, "danger")
        return render_template("upload_jd.html")

    # --- Handle optional file upload ---
    jd_file_key = None
    jd_original_filename = None
    jd_content_type = None

    uploaded_file = request.files.get("jd_file")
    has_file = uploaded_file and uploaded_file.filename

    if has_file:
        suffix = Path(uploaded_file.filename).suffix.lower()
        if suffix not in _JD_ALLOWED_SUFFIXES:
            flash(
                f"Unsupported file type '{suffix}'. Only PDF or DOCX are accepted.",
                "danger",
            )
            return render_template("upload_jd.html")

        jd_original_filename = uploaded_file.filename
        jd_content_type = _JD_CONTENT_TYPES[suffix]
        file_bytes = uploaded_file.read()

        # Generate jd_id early so the deterministic S3 key can be built.
        jd_id = f"jd_{uuid.uuid4().hex[:8]}"

        # Build deterministic key — same jd_id + same extension always writes
        # to the same S3 object, overwriting any previous upload.
        jd_file_key = s3_storage.jd_key(jd_id, suffix)

        # Upload original file to S3 (private, overwrites previous if any).
        try:
            s3_storage.upload_file(file_bytes, jd_file_key, jd_content_type)
        except Exception as e:
            flash(f"Could not store JD document in S3: {e}", "danger")
            return render_template("upload_jd.html")

        # Extract text from the uploaded document bytes.
        try:
            stored_bytes = s3_storage.download_file(jd_file_key)
            jd_text = extract_text_from_bytes(stored_bytes, jd_original_filename)
        except Exception as e:
            flash(f"Could not extract text from JD document: {e}", "danger")
            return render_template("upload_jd.html")

        if not jd_text:
            flash("Could not extract any text from the uploaded document.", "danger")
            return render_template("upload_jd.html")
    else:
        # No file — typed text is required.
        if not jd_text:
            flash(
                "Please either paste the Job Description text or upload a PDF/DOCX file.",
                "danger",
            )
            return render_template("upload_jd.html")
        jd_id = f"jd_{uuid.uuid4().hex[:8]}"

    jd = JobDescription(
        jd_id=jd_id,
        role_title=role_title,
        company=company,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        experience_years=int(experience_years),
        jd_text=jd_text,
        location=location,
        is_active=True,
        uploaded_by=get_current_user_id(),
        created_at=utc_now_iso(),
        jd_file_key=jd_file_key,
        jd_original_filename=jd_original_filename,
        jd_content_type=jd_content_type,
        **salary_fields,
    )

    jds = _load_jds()
    jds.append(jd.to_dict())
    _save_jds(jds)

    _audit.info("jd.uploaded", extra={
        "jd_id": jd.jd_id, "role_title": role_title, "company": company,
        "actor": get_current_user_id(), "role": session.get("role", ""),
        "via_file": has_file,
    })
    flash(f"JD uploaded: {role_title} at {company}", "success")
    return redirect(url_for("admin.admin_dashboard"))


@admin.route("/jds")
@login_required
@admin_required
def list_jds():
    """List all job descriptions."""
    jds = _load_jds()
    return render_template("jd_list.html", jds=jds)


@admin.route("/jds/<jd_id>")
@login_required
@admin_required
def view_jd(jd_id):
    """View a single JD in detail."""
    jds = _load_jds()
    jd = next((j for j in jds if j["jd_id"] == jd_id), None)
    if not jd:
        flash("JD not found.", "danger")
        return redirect(url_for("admin.admin_dashboard"))
    return render_template("jd_detail.html", jd=jd, admin_names=_admin_names())


@admin.route("/jds/<jd_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_jd(jd_id):
    """Edit an existing JD."""
    jds = _load_jds()
    jd = next((j for j in jds if j["jd_id"] == jd_id), None)
    if not jd:
        flash("JD not found.", "danger")
        return redirect(url_for("admin.admin_dashboard"))

    if request.method == "GET":
        return render_template("edit_jd.html", jd=jd)

    # Update structured fields.
    jd["role_title"] = request.form.get("role_title", jd["role_title"]).strip()
    jd["company"] = request.form.get("company", jd["company"]).strip()
    jd["required_skills"] = [s.strip() for s in request.form.get("required_skills", "").split(",") if s.strip()]
    jd["preferred_skills"] = [s.strip() for s in request.form.get("preferred_skills", "").split(",") if s.strip()]
    jd["experience_years"] = int(request.form.get("experience_years", jd["experience_years"]))
    jd["location"] = request.form.get("location", jd["location"]).strip()

    salary_fields, salary_error = parse_salary_form(request.form)
    if salary_error:
        flash(salary_error, "danger")
        return render_template("edit_jd.html", jd=jd)
    jd.update(salary_fields)

    # Optional: admin may supply a replacement document file.
    # If provided, overwrite the same deterministic S3 key and replace jd_text.
    # jd_original_filename and jd_content_type reflect only the current file.
    updated_file = request.files.get("jd_file")
    has_updated_file = updated_file and updated_file.filename

    if has_updated_file:
        suffix = Path(updated_file.filename).suffix.lower()
        if suffix not in _JD_ALLOWED_SUFFIXES:
            flash(
                f"Unsupported file type '{suffix}'. Only PDF or DOCX are accepted.",
                "danger",
            )
            return render_template("edit_jd.html", jd=jd)

        file_bytes = updated_file.read()
        content_type = _JD_CONTENT_TYPES[suffix]
        # Use the existing jd_id to build the same deterministic key — this
        # overwrites the previous S3 object without creating a new one.
        new_file_key = s3_storage.jd_key(jd_id, suffix)

        try:
            s3_storage.upload_file(file_bytes, new_file_key, content_type)
        except Exception as e:
            flash(f"Could not update JD document in S3: {e}", "danger")
            return render_template("edit_jd.html", jd=jd)

        try:
            stored_bytes = s3_storage.download_file(new_file_key)
            extracted = extract_text_from_bytes(stored_bytes, updated_file.filename)
        except Exception as e:
            flash(f"Could not extract text from updated document: {e}", "danger")
            return render_template("edit_jd.html", jd=jd)

        if not extracted:
            flash("Could not extract any text from the updated document.", "danger")
            return render_template("edit_jd.html", jd=jd)

        # Replace all JD document fields with the new upload.
        jd["jd_text"] = extracted
        jd["jd_file_key"] = new_file_key
        jd["jd_original_filename"] = updated_file.filename
        jd["jd_content_type"] = content_type
    else:
        # No replacement file — use the typed text from the form if provided.
        typed_text = request.form.get("jd_text", "").strip()
        if typed_text:
            jd["jd_text"] = typed_text

    _mark_updated(jd)
    _save_jds(jds)
    _audit.info("jd.edited", extra={
        "jd_id": jd_id, "role_title": jd.get("role_title"),
        "actor": get_current_user_id(), "role": session.get("role", ""),
    })
    flash("JD updated successfully.", "success")
    return redirect(url_for("admin.admin_dashboard"))


@admin.route("/jds/<jd_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_jd(jd_id):
    """Deactivate a JD (soft delete)."""
    jds = _load_jds()
    role_title = None
    for j in jds:
        if j["jd_id"] == jd_id:
            j["is_active"] = False
            role_title = j.get("role_title")
            _mark_updated(j)
            break
    _save_jds(jds)
    _audit.info("jd.deactivated", extra={
        "jd_id": jd_id, "role_title": role_title,
        "actor": get_current_user_id(), "role": session.get("role", ""),
    })
    flash("JD deactivated.", "info")
    return redirect(url_for("admin.admin_dashboard"))


@admin.route("/jds/<jd_id>/reactivate", methods=["POST"])
@login_required
@admin_required
def reactivate_jd(jd_id):
    """Reactivate a previously deactivated JD."""
    jds = _load_jds()
    role_title = None
    for j in jds:
        if j["jd_id"] == jd_id:
            j["is_active"] = True
            role_title = j.get("role_title")
            _mark_reposted(j)
            break
    _save_jds(jds)
    _audit.info("jd.reactivated", extra={
        "jd_id": jd_id, "role_title": role_title,
        "actor": get_current_user_id(), "role": session.get("role", ""),
    })
    flash("JD reactivated.", "success")
    return redirect(url_for("admin.admin_dashboard"))


@admin.route("/candidates")
@login_required
@admin_required
def list_candidates():
    """List all registered candidates."""
    candidates = get_all_candidates()
    return render_template("candidate_list.html", candidates=candidates)


@admin.route("/candidates-for-jd/<jd_id>")
@login_required
@admin_required
def candidates_for_jd(jd_id):
    """Show all candidates ranked against a specific JD."""
    jds = _load_jds()
    target_jd = next((j for j in jds if j["jd_id"] == jd_id), None)
    if not target_jd:
        flash("JD not found.", "danger")
        return redirect(url_for("admin.admin_dashboard"))

    candidates = get_all_candidates()

    def _load_parsed(cid):
        """Load a candidate's parsed resume JSON, or None."""
        p = PARSED_RESUMES_DIR / f"{cid}.json"
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)

    # Two-stage funnel: embeddings shortlist (all) → LLM explanation (top-K).
    model_id = session.get("selected_model")  # UI model picker (Stage 4); None = default
    ranked = two_stage_match(target_jd, candidates, _load_parsed, model_id=model_id)

    return render_template("ranked_candidates.html", jd=target_jd, ranked=ranked)


# ─────────────────────────────────────────────
# MODEL PICKER + USERS/ROLES (Stage 4)
# ─────────────────────────────────────────────

@admin.route("/models")
@login_required
@admin_required
def models():
    """List all registered models with their status; allow selecting active default per session."""
    from db.database import get_session
    from db.models_orm import ModelRegistry
    with get_session() as s:
        rows = [
            {"model_id": m.model_id, "family": m.family, "purpose": m.purpose,
             "status": m.status, "display_name": m.display_name, "is_default": m.is_default}
            for m in s.query(ModelRegistry).order_by(ModelRegistry.purpose).all()
        ]
    return render_template("models.html", models=rows,
                           selected=session.get("selected_model"))


@admin.route("/models/select", methods=["POST"])
@login_required
@admin_required
def select_model():
    """Set the session's preferred LLM model (only if active)."""
    from db.database import get_session
    from db.models_orm import ModelRegistry
    model_id = request.form.get("model_id", "").strip()
    with get_session() as s:
        row = s.query(ModelRegistry).filter_by(model_id=model_id).first()
        if not row:
            flash("Model not found.", "danger")
        elif row.status != "active":
            flash(f"Model '{model_id}' is {row.status}, cannot select.", "danger")
        else:
            session["selected_model"] = model_id
            _audit.info("model.selected", extra={"model_id": model_id,
                                                 "actor": get_current_user_id()})
            flash(f"Model set to {model_id} for LLM tasks.", "success")
    return redirect(url_for("admin.models"))


@admin.route("/models/<model_id>/status", methods=["POST"])
@login_required
@admin_required
def set_model_status(model_id):
    """Change a model's lifecycle status (active/inactive/retired)."""
    from db.database import get_session
    from db.models_orm import ModelRegistry
    new_status = request.form.get("status", "").strip()
    if new_status not in ("active", "inactive", "retired"):
        flash("Invalid status.", "danger")
        return redirect(url_for("admin.models"))
    with get_session() as s:
        row = s.query(ModelRegistry).filter_by(model_id=model_id).first()
        if row:
            row.status = new_status
    _audit.info("model.status_changed", extra={"model_id": model_id, "status": new_status,
                                                "actor": get_current_user_id()})
    flash(f"{model_id} -> {new_status}", "info")
    return redirect(url_for("admin.models"))


@admin.route("/users")
@login_required
@admin_required
def users():
    """List all candidate + admin accounts with their roles."""
    from auth import load_json
    from config import DATA_DIR
    candidates = load_json(DATA_DIR / "candidates.json")
    admins = load_json(DATA_DIR / "admins.json")
    return render_template("users.html", candidates=candidates, admins=admins)
