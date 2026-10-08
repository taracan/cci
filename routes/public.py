"""Public routes — landing page, registration, login, logout, health check."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from routes.candidate import store_resume_for_candidate
from auth import (
    register_candidate,
    register_admin,
    authenticate_user,
    set_session,
    clear_session,
)

public = Blueprint("public", __name__)


@public.route("/")
def index():
    """Landing page."""
    from flask import session
    if "user_id" in session:
        return redirect(url_for("public.dashboard"))
    return render_template("index.html")


@public.route("/register", methods=["GET", "POST"])
def register():
    """Register a new candidate or admin."""
    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    target_role = request.form.get("target_role", "").strip()
    role = request.form.get("role", "candidate")
    admin_key = request.form.get("admin_key", "")
    resume_file = request.files.get("resume")

    if not name or not email or not password:
        flash("All fields are required.", "danger")
        return render_template("register.html")

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return render_template("register.html")

    if role == "admin":
        success, message, user_id = register_admin(name, email, password, admin_key)
    else:
        if not target_role:
            flash("Target role is required for candidates.", "danger")
            return render_template("register.html")
        success, message, user_id = register_candidate(name, email, password, target_role)

    if not success:
        flash(message, "danger")
        return render_template("register.html")

    set_session(user_id, name, role)
    flash(message, "success")

    if role == "candidate":
        if resume_file and resume_file.filename:
            from flask import session
            candidate_id = session.get("user_id")
            ok, result = store_resume_for_candidate(candidate_id, resume_file)
            if not ok:
                flash(result, "danger")
                return redirect(url_for("candidate.candidate_dashboard"))
            flash(result["message"], "success")
            return redirect(url_for("candidate.candidate_dashboard"))
        return redirect(url_for("candidate.match_jobs"))

    return redirect(url_for("public.dashboard"))


@public.route("/login", methods=["GET", "POST"])
def login():
    """Login with email and password."""
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    success, user_id, name, role = authenticate_user(email, password)

    if not success:
        flash("Invalid email or password.", "danger")
        return render_template("login.html")

    set_session(user_id, name, role)
    flash(f"Welcome back, {name}!", "success")
    return redirect(url_for("public.dashboard"))


@public.route("/logout")
def logout():
    """Clear session and redirect to landing."""
    clear_session()
    flash("Logged out successfully.", "info")
    return redirect(url_for("public.index"))


@public.route("/dashboard")
def dashboard():
    """Redirect to appropriate dashboard based on role."""
    from flask import session
    if "user_id" not in session:
        return redirect(url_for("public.login"))
    if session.get("role") == "admin":
        return redirect(url_for("admin.admin_dashboard"))
    return redirect(url_for("candidate.candidate_dashboard"))


@public.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "Resume-JD Matching Portal", "version": "1.0"}
