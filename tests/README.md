# TalentLink Portal — Test Suite

All unit and integration tests for the portal live in this folder. Tests are
fast, isolated, and offline: they never touch the real Postgres database, the
real Ollama server, or the real embedding model.

## Running the tests

```bash
cd resume_jd_portal
pip install pytest            # once
python3 -m pytest             # run everything
python3 -m pytest -q          # quiet
python3 -m pytest tests/test_factory.py            # one file
python3 -m pytest tests/test_routes.py -k lifecycle  # one pattern
python3 -m pytest -v          # verbose (see every test name)
```

Config lives in `../pytest.ini` (`testpaths = tests`).

## How isolation works (read this before writing new tests)

The app reads `DATABASE_URL` from `config.py` **at import time**, and
`db/database.py` builds the SQLAlchemy `engine` from it — also at import time.
So `conftest.py` does two things **before any app module is imported**:

1. Sets `os.environ["DATABASE_URL"] = "sqlite:///:memory:"` at the very top.
2. Rebuilds `db.database.engine` / `SessionLocal` on a **shared** in-memory
   SQLite using `StaticPool`, so every `get_session()` sees the same tables
   (a plain `:memory:` URL gives each connection its own empty DB).

If you ever see a test hit real Postgres, it means something imported
`db.database` before `conftest.py` ran — keep the env line at the top of
`conftest.py`.

## Fixtures available (from `conftest.py`)

| Fixture | What it gives you |
|---|---|
| `db_session` | A session on the shared in-memory DB; creates all tables, drops them after the test. |
| `seeded_models` | `db_session` + two `ModelRegistry` rows (one `active`, one `inactive`). |
| `fake_llm` | Patches `llm.llm_service.get_llm_client` with a fake client. Set `fake_llm.response` to the raw text the "model" should return. |
| `app` | The Flask app built via `main.create_app()` (in-memory DB). |
| `client` | A Flask test client for `app`. |

For route tests that register/login users, use the `isolated_client` fixture
defined in `test_routes.py` — it redirects the `candidates.json` / `admins.json`
/ `jds.json` stores into a `tmp_path` so the real data files are never written.

## What each test file covers

| File | Module under test | Focus |
|---|---|---|
| `test_matching_engine.py` | `matching_engine.py` | Skill/experience/role/overall scoring, both weighting modes |
| `test_matching.py` | `matching_engine.py` | Original scoring tests (kept for extra coverage) |
| `test_auth.py` | `auth.py` | Password hashing, JSON persistence, register/login |
| `test_tracing.py` | `observability/tracing.py` | trace_id constant per request, span_id per stage |
| `test_llm_service.py` | `llm/llm_service.py` | `_safe_json`, resume/JD parse, `explain_match` (via `fake_llm`) |
| `test_factory.py` | `llm/factory.py` | Model-status gate: active resolves, inactive/missing raises |
| `test_queue_manager.py` | `llm/queue_manager.py` | Slot acquire/release, `QueueFullError` on TTL, release on error |
| `test_vector_matcher.py` | `vector_matcher.py` | Empty-input guards, id lookup, distance→score (model + Chroma faked) |
| `test_pipeline_matcher.py` | `pipeline/matcher.py` | Two-stage funnel: all scored → LLM on top-K only → sorted |
| `test_db_models.py` | `db/models_orm.py` | ORM create/query + defaults for all 5 tables |
| `test_routes.py` | `routes/*.py` | Health, register/login lifecycle, admin JD lifecycle |

## Adding tests for a NEW feature

When you add a new module (say `llm/reranker.py`), add `tests/test_reranker.py`:

1. **Name it `test_<module>.py`** so pytest auto-discovers it.
2. **Put the sys.path shim at the top** so the portal package imports:
   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
   ```
3. **Fake anything external.** No test may call real Ollama, real Postgres, or
   download a model. Patterns already in the suite:
   - LLM → `fake_llm` fixture, or `monkeypatch.setattr(module, "explain_match", fake)`.
   - Embedding model / ChromaDB → see `fake_vectors` in `test_vector_matcher.py`.
   - DB → use `db_session` / `seeded_models`.
   - Flask routes → use `client` or the `isolated_client` pattern.
4. **Group related cases in `Test<Thing>` classes** with small, single-assert
   methods where possible.
5. **Test the unhappy paths too**: bad JSON, missing rows, empty inputs, the
   error/exception branches — not just the success case.
6. **Run `python3 -m pytest tests/test_<module>.py`** and make it green before
   committing.

### Quick template

```python
"""Unit tests for <module> — <one line>."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from mymodule import my_function


class TestMyFunction:
    def test_happy_path(self):
        assert my_function(2) == 4

    def test_bad_input_raises(self):
        with pytest.raises(ValueError):
            my_function(-1)
```

## Conventions

- Tests must be deterministic and order-independent.
- Never write to real `data/`, `logs/`, or the Postgres DB from a test.
- Keep the whole suite fast (seconds). Mark anything genuinely slow with
  `@pytest.mark.slow` and skip it by default if you add such tests.
