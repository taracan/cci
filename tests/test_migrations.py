"""Alembic migrations stay in sync with the ORM models.

The rest of the suite builds tables with create_all() on in-memory SQLite (see
conftest.py), so this is the one test that runs the real migrations: upgrade a
fresh SQLite file to head, assert it matches Base.metadata, then downgrade.
"""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from db.database import Base
import db.models_orm  # noqa: F401  register models

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config():
    # No ini file on purpose: env.py then skips fileConfig(), which would
    # otherwise reconfigure the app's logging for the rest of the test run.
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def test_single_head():
    assert len(ScriptDirectory.from_config(_alembic_config()).get_heads()) == 1


def test_upgrade_matches_models_and_downgrade_is_clean(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
    monkeypatch.setattr("config.DATABASE_URL", url)   # env.py reads this
    cfg = _alembic_config()
    engine = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], f"models and migrations differ: {diff}"

        command.downgrade(cfg, "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
    finally:
        engine.dispose()
