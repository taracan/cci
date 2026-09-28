"""Shared pytest fixtures for the TalentLink portal test suite.

Design goals:
  - Fast + isolated: DB uses in-memory SQLite, data files use a tmp dir.
  - No external services: Ollama and the embedding model are faked.
  - Easy to extend: add a new test_<module>.py and reuse these fixtures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# CRITICAL: set the DB URL BEFORE importing anything that reads config, so the
# SQLAlchemy engine binds to an isolated in-memory SQLite — never the real
# Postgres dev database. This must run at import time (before db.database).
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest

# Make the portal package importable (tests/ is one level below the app root).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _rebuild_engine_for_sqlite_memory():
    """Rebuild db.database.engine/SessionLocal on a SHARED in-memory SQLite.

    A plain sqlite:///:memory: gives each new connection its OWN empty database,
    so tables created in one session are invisible to the next. Using StaticPool
    with a single shared connection keeps all sessions on the same in-memory DB
    for the whole test process.
    """
    import db.database as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    dbmod.engine = engine
    dbmod.SessionLocal = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    return engine


# Rebuild the shared engine once, at import time, after db.database exists.
_ENGINE = _rebuild_engine_for_sqlite_memory()


@pytest.fixture(autouse=True)
def _isolated_vector_store(tmp_path, monkeypatch):
    """Keep every test away from the real data/chroma store and MiniLM model.

    Routes call index_jd / index_resume / semantic_score directly, so without
    this any route test would write vectors into data/chroma and download the
    embedding model. Chroma is pointed at a per-test tmp dir (created lazily on
    first use) and the model is replaced by a deterministic fake.
    """
    import hashlib
    import vector_matcher

    class _Vec(list):
        def tolist(self):
            return list(self)

    class _FakeModel:
        def encode(self, text):
            digest = hashlib.sha256(text.encode()).digest()
            return _Vec(b / 255.0 for b in digest[:16])

    monkeypatch.setattr(vector_matcher, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(vector_matcher, "_client", None)
    monkeypatch.setattr(vector_matcher, "_resume_collection", None)
    monkeypatch.setattr(vector_matcher, "_jd_collection", None)
    monkeypatch.setattr(vector_matcher, "_get_model", lambda: _FakeModel())


@pytest.fixture()
def db_session():
    """Create all tables in the shared in-memory DB and yield a session.

    Tables are dropped after each test so every test starts from a clean slate.
    """
    import db.database as dbmod
    from db.database import Base
    import db.models_orm  # noqa: F401  register models
    Base.metadata.create_all(bind=dbmod.engine)
    with dbmod.get_session() as s:
        yield s
    Base.metadata.drop_all(bind=dbmod.engine)


@pytest.fixture()
def seeded_models(db_session):
    """Insert a couple of registry models (one active, one inactive)."""
    from db.models_orm import ModelRegistry
    db_session.add_all([
        ModelRegistry(model_id="llama3.2:latest", family="llama", provider="ollama",
                      purpose="reasoning", status="active", is_default=True),
        ModelRegistry(model_id="retired-model", family="x", provider="ollama",
                      purpose="reasoning", status="inactive", is_default=False),
    ])
    db_session.flush()
    return db_session


@pytest.fixture()
def fake_llm(monkeypatch):
    """Replace the Ollama client with a fake returning canned JSON.

    Usage: set fake_llm.response before calling the service under test.
    """
    from llm.base import LlmResult

    class _Holder:
        response = '{"skills": ["Python"], "experience_years": 3, "education": "B.Tech", "summary": "ok"}'

    holder = _Holder()

    class FakeClient:
        def __init__(self, model_id="llama3.2:latest"):
            self.model_id = model_id

        def generate(self, prompt):
            return LlmResult(text=holder.response, prompt_tokens=10,
                             completion_tokens=5, model_id=self.model_id)

    # Patch the factory so llm_service uses the fake, bypassing the DB gate.
    monkeypatch.setattr("llm.llm_service.get_llm_client",
                        lambda model_id=None: FakeClient(model_id or "llama3.2:latest"))
    return holder


@pytest.fixture()
def app():
    """Build the Flask app with an in-memory DB for route testing."""
    import main
    application = main.create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()
