"""Authentication module — password hashing, session management, role-based access."""
import json
import uuid
from datetime import datetime
from pathlib import Path
from functools import wraps

import bcrypt
from flask import session, flash, redirect, url_for

from config import DATA_DIR, ADMIN_KEY
from models import Candidate, Admin


CANDIDATES_FILE = DATA_DIR / "candidates.json"
ADMINS_FILE = DATA_DIR / "admins.json"


# --- JSON persistence ---

def load_json(path):
    """Load a JSON file, return empty list if not found."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r") as f:
        return json.load(f)


def save_json(path, data):
    """Save data to a JSON file, creating parent dirs if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


# --- Password hashing ---

def hash_password(password):
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password, hashed):
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


# --- Registration ---

def register_candidate(name, email, password, target_role):
    """Register a new candidate. Returns (success, message, user_id)."""
    candidates = load_json(CANDIDATES_FILE)

    if any(c["email"] == email for c in candidates):
        return False, "Email already registered.", None

    user_id = f"cand_{uuid.uuid4().hex[:8]}"
    candidate = Candidate(
        candidate_id=user_id,
        name=name,
        email=email,
        password_hash=hash_password(password),
        target_role=target_role,
        registered_at=datetime.utcnow().isoformat(),
    )
    candidates.append(candidate.to_dict())
    save_json(CANDIDATES_FILE, candidates)
    return True, "Registration successful!", user_id


def register_admin(name, email, password, admin_key):
    """Register a new admin. Returns (success, message, user_id)."""
    if admin_key != ADMIN_KEY:
        return False, "Invalid admin key.", None

    admins = load_json(ADMINS_FILE)
    candidates = load_json(CANDIDATES_FILE)

    if any(a["email"] == email for a in admins) or any(c["email"] == email for c in candidates):
        return False, "Email already registered.", None

    user_id = f"admin_{uuid.uuid4().hex[:8]}"
    admin = Admin(
        admin_id=user_id,
        name=name,
        email=email,
        password_hash=hash_password(password),
        registered_at=datetime.utcnow().isoformat(),
    )
    admins.append(admin.to_dict())
    save_json(ADMINS_FILE, admins)
    return True, "Registration successful!", user_id


# --- Login ---

def authenticate_user(email, password):
    """Authenticate by email + password. Returns (success, user_id, name, role)."""
    # Check candidates
    for c in load_json(CANDIDATES_FILE):
        if c["email"] == email:
            if verify_password(password, c["password_hash"]):
                return True, c["candidate_id"], c["name"], "candidate"
            return False, None, None, None

    # Check admins
    for a in load_json(ADMINS_FILE):
        if a["email"] == email:
            if verify_password(password, a["password_hash"]):
                return True, a["admin_id"], a["name"], "admin"
            return False, None, None, None

    return False, None, None, None


# --- Session helpers ---

def set_session(user_id, name, role):
    """Store user info in Flask session."""
    session["user_id"] = user_id
    session["name"] = name
    session["role"] = role


def clear_session():
    """Clear all session data."""
    session.clear()


def get_current_user_id():
    """Return current user_id from session or None."""
    return session.get("user_id")


def get_current_role():
    """Return current role from session or None."""
    return session.get("role")


# --- Decorators ---

def login_required(f):
    """Decorator: redirect to login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("public.login"))
        return f(*args, **kwargs)
    return decorated


def candidate_required(f):
    """Decorator: restrict to candidate role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "candidate":
            flash("Access denied. Candidate only.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: restrict to admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Access denied. Admin only.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


# --- Candidate data access ---

def get_candidate(candidate_id):
    """Load a single candidate record by ID."""
    for c in load_json(CANDIDATES_FILE):
        if c["candidate_id"] == candidate_id:
            return c
    return None


def get_all_candidates():
    """Load all candidate records."""
    return load_json(CANDIDATES_FILE)


def update_candidate(candidate_id, updates):
    """Update fields on a candidate record."""
    candidates = load_json(CANDIDATES_FILE)
    for c in candidates:
        if c["candidate_id"] == candidate_id:
            c.update(updates)
            break
    save_json(CANDIDATES_FILE, candidates)
