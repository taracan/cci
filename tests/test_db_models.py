"""Unit tests for the SQLAlchemy ORM models (db.models_orm).

Covers create + query round-trips and defaults for all five tables:
ModelRegistry, LlmCall, RequestTrace, Role, UserAccount (incl. the role
relationship).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models_orm import ModelRegistry, LlmCall, RequestTrace, Role, UserAccount


class TestModelRegistry:
    def test_create_and_defaults(self, db_session):
        m = ModelRegistry(model_id="m1", family="llama", provider="ollama")
        db_session.add(m)
        db_session.flush()
        got = db_session.query(ModelRegistry).filter_by(model_id="m1").one()
        assert got.status == "active"          # default
        assert got.is_default is False         # default
        assert got.max_tokens == 4096          # default
        assert "m1" in repr(got)

    def test_unique_model_id(self, db_session):
        import pytest
        from sqlalchemy.exc import IntegrityError
        db_session.add(ModelRegistry(model_id="dup", family="x"))
        db_session.flush()
        db_session.add(ModelRegistry(model_id="dup", family="y"))
        with pytest.raises(IntegrityError):
            db_session.flush()
        # The session is now in a failed state; roll back so fixture teardown
        # (which commits + drops tables) doesn't hit a PendingRollbackError.
        db_session.rollback()


class TestLlmCall:
    def test_create_and_query(self, db_session):
        call = LlmCall(trace_id="t1", span_id="s1", model_id="llama3.2:latest",
                       task="parse_resume", prompt_tokens=12, completion_tokens=8)
        db_session.add(call)
        db_session.flush()
        got = db_session.query(LlmCall).filter_by(trace_id="t1").one()
        assert got.task == "parse_resume"
        assert got.status == "ok"              # default
        assert got.prompt_tokens == 12


class TestRequestTrace:
    def test_create_and_defaults(self, db_session):
        rt = RequestTrace(trace_id="trace-1", route="/x", method="POST")
        db_session.add(rt)
        db_session.flush()
        got = db_session.query(RequestTrace).filter_by(trace_id="trace-1").one()
        assert got.method == "POST"
        assert got.status_code == 0            # default


class TestRoleAndUser:
    def test_role_user_relationship(self, db_session):
        role = Role(name="admin", permissions='["all"]')
        db_session.add(role)
        db_session.flush()

        user = UserAccount(user_id="u1", name="Alice", email="alice@x.com",
                           role_id=role.id)
        db_session.add(user)
        db_session.flush()

        got = db_session.query(UserAccount).filter_by(email="alice@x.com").one()
        assert got.is_active is True           # default
        assert got.role.name == "admin"        # relationship back-populates
        assert got in role.users
