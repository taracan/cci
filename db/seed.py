"""Seed the model registry with installed Ollama models and default RBAC roles."""
from __future__ import annotations

import json

from db.database import get_session, init_db
from db.models_orm import ModelRegistry, Role

# Models actually installed locally (from `ollama list`).
_SEED_MODELS = [
    {"model_id": "llama3.2:latest", "family": "llama", "purpose": "reasoning",
     "display_name": "Llama 3.2 (fast)", "is_default": True, "status": "active"},
    {"model_id": "llama3:latest", "family": "llama", "purpose": "reasoning",
     "display_name": "Llama 3 (balanced)", "is_default": False, "status": "active"},
    {"model_id": "deepseek-r1:latest", "family": "deepseek", "purpose": "reasoning",
     "display_name": "DeepSeek R1 (deep reasoning)", "is_default": False, "status": "active"},
    {"model_id": "deepseek-r1:1.5b", "family": "deepseek", "purpose": "reasoning",
     "display_name": "DeepSeek R1 1.5B (tiny)", "is_default": False, "status": "inactive"},
    {"model_id": "nomic-embed-text:latest", "family": "nomic", "purpose": "embedding",
     "display_name": "Nomic Embed Text", "is_default": False, "status": "active"},
]

_SEED_ROLES = [
    {"name": "admin", "permissions": ["*"]},
    {"name": "recruiter", "permissions": ["view_jds", "view_candidates", "rank_candidates"]},
    {"name": "candidate", "permissions": ["upload_resume", "match_jobs", "view_profile"]},
]


def seed() -> None:
    """Create tables (if needed) and insert model + role rows idempotently."""
    init_db()
    with get_session() as s:
        for m in _SEED_MODELS:
            exists = s.query(ModelRegistry).filter_by(model_id=m["model_id"]).first()
            if not exists:
                s.add(ModelRegistry(**m))

        for r in _SEED_ROLES:
            exists = s.query(Role).filter_by(name=r["name"]).first()
            if not exists:
                s.add(Role(name=r["name"], permissions=json.dumps(r["permissions"])))

    print("Seed complete: model_registry + role tables populated.")


if __name__ == "__main__":
    seed()
