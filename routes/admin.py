"""Admin routes — dashboard, upload JD, manage JDs, view ranked candidates."""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response

from auth import login_required, admin_required, get_all_candidates, get_current_user_id, load_json, save_json
from config import JDS_DIR, PARSED_RESUMES_DIR
from matching_engine import calculate_match
from models import JobDescription
from vector_matcher import semantic_score, index_jd, delete_jd as delete_jd_embedding
from pipeline.matcher import two_stage_match
from observability.logging_config import get_logger

admin: Blueprint = Blueprint("admin", __name__, url_prefix="/admin")

_audit: Any = get_logger("admin.action")

JDS_FILE: Path = JDS_DIR / "jds.json"


def _load_jds() -> list[dict[str, Any]]:
    """Load all JDs from file.

    Returns:
        list[dict[str, Any]]: List of job description dictionaries.

    Raises:
        FileNotFoundError: If JDS_FILE does not exist.
        json.JSONDecodeError: If file is not valid JSON.
    """
    return load_json(JDS_FILE)


def _save_jds(jds: list[dict[str, Any]]) -> None:
    """Save JDs to file.

    Args:
        jds: List of job description dictionaries to persist.

    Returns:
        None

    Raises:
        IOError: If file write operation fails.
    """
    save_json(JDS_FILE, jds)


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
def upload_jd() -> Response | str:
    """Upload a new Job Description.

    GET: Render upload form
    POST: Process form submission, create JD, embed, and persist

    Form Parameters:
        role_title (str): Job role title (required)
        company (str): Company name (required)
        required_skills (str): Comma-separated required skills (required)
        preferred_skills (str): Comma-separated preferred skills
        experience_years (int): Required years of experience
        jd_text (str): Full job description text (required)
        location (str): Job location (default: "Remote")

    Returns:
        Response: Redirect to dashboard on success, or re-render form on validation failure
        str: HTML form template on GET request

    Side Effects:
        - Creates JD record in jds.json
        - Embeds JD text and stores in ChromaDB
        - Logs audit event
    """
    if request.method == "GET":
        return render_template("upload_jd.html")

    role_title: str = request.form.get("role_title", "").strip()
    company: str = request.form.get("company", "").strip()
    required_skills_raw: str = request.form.get("required_skills", "")
    preferred_skills_raw: str = request.form.get("preferred_skills", "")
    experience_years: str = request.form.get("experience_years", "0")
    jd_text: str = request.form.get("jd_text", "").strip()
    location: str = request.form.get("location", "Remote").strip()

    required_skills: list[str] = [s.strip() for s in required_skills_raw.split(",") if s.strip()]
    preferred_skills: list[str] = [s.strip() for s in preferred_skills_raw.split(",") if s.strip()]

    # Validation
    if not role_title or not company or not required_skills or not jd_text:
        flash("Please fill all required fields (Role, Company, Required Skills, JD Text).", "danger")
        return render_template("upload_jd.html")

    # Create JD object
    jd: JobDescription = JobDescription(
        jd_id=f"jd_{uuid.uuid4().hex[:8]}",
        role_title=role_title,
        company=company,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        experience_years=int(experience_years),
        jd_text=jd_text,
        location=location,
        is_active=True,
        uploaded_by=get_current_user_id(),
        created_at=datetime.utcnow().isoformat(),
    )

    # Persist to file
    jds: list[dict[str, Any]] = _load_jds()
    jds.append(jd.to_dict())
    _save_jds(jds)

    # Embed and index in ChromaDB (graceful failure on error)
    try:
        index_jd(jd.jd_id, jd_text)
    except Exception as e:
        _audit.warning(f"Failed to index JD {jd.jd_id}: {e}")

    # Audit log
    _audit.info("jd.uploaded", extra={
        "jd_id": jd.jd_id, "role_title": role_title, "company": company,
        "actor": get_current_user_id(), "role": session.get("role", ""),
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
    return render_template("jd_detail.html", jd=jd)


@admin.route("/jds/<jd_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_jd(jd_id: str) -> Response | str:
    """Edit an existing JD.

    GET: Render edit form with current JD data
    POST: Process form submission, update JD, re-embed, and persist

    Args:
        jd_id: Unique job description ID to edit

    Form Parameters:
        role_title (str): Updated job role title
        company (str): Updated company name
        required_skills (str): Updated comma-separated required skills
        preferred_skills (str): Updated comma-separated preferred skills
        experience_years (int): Updated years of experience requirement
        jd_text (str): Updated full job description text
        location (str): Updated job location

    Returns:
        Response: Redirect to dashboard on success
        str: HTML form template on GET or validation failure

    Side Effects:
        - Updates JD record in jds.json
        - Re-embeds JD text and updates ChromaDB
        - Logs audit event
    """
    jds: list[dict[str, Any]] = _load_jds()
    jd: dict[str, Any] | None = next((j for j in jds if j["jd_id"] == jd_id), None)

    if not jd:
        flash("JD not found.", "danger")
        return redirect(url_for("admin.admin_dashboard"))

    if request.method == "GET":
        return render_template("edit_jd.html", jd=jd)

    # Update fields from form
    jd["role_title"] = request.form.get("role_title", jd["role_title"]).strip()
    jd["company"] = request.form.get("company", jd["company"]).strip()
    jd["required_skills"] = [
        s.strip() for s in request.form.get("required_skills", "").split(",") if s.strip()
    ]
    jd["preferred_skills"] = [
        s.strip() for s in request.form.get("preferred_skills", "").split(",") if s.strip()
    ]
    jd["experience_years"] = int(request.form.get("experience_years", jd["experience_years"]))
    jd["jd_text"] = request.form.get("jd_text", jd["jd_text"]).strip()
    jd["location"] = request.form.get("location", jd["location"]).strip()

    # Persist to file
    _save_jds(jds)

    # Re-embed and index in ChromaDB (graceful failure on error)
    try:
        index_jd(jd_id, jd["jd_text"])
    except Exception as e:
        _audit.warning(f"Failed to re-index JD {jd_id}: {e}")

    # Audit log
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
