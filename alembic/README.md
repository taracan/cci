# Database migrations (Alembic)

Alembic is the **only** way the database schema changes. Run every command from
the repo root. The DB URL comes from `config.DATABASE_URL` (AWS → env var →
default), the same as the app, so it is never set in `alembic.ini`.

The app does **not** create or migrate tables on startup. Run
`alembic upgrade head` explicitly when you set up a database and after every pull.

## Revisions
| Revision | Contents |
|---|---|
| `0001` | Baseline: the 5 tables that existed before Alembic (`model_registry`, `llm_call`, `request_trace`, `role`, `user_account`) |
| `0002` | Multi-tenant foundation: `company`, `user_account.company_id`, `job_description` (ownership only), `application` |

## Team workflow (every schema change)
```
# 1. edit db/models_orm.py
# 2. generate a migration by diffing models vs your DB (which must be at head):
alembic revision --autogenerate -m "describe the change"
# 3. REVIEW the file in alembic/versions/ (autogenerate isn't perfect;
#    give new constraints explicit names)
# 4. apply it:
alembic upgrade head
# 5. commit the migration file with your code PR
```
- Catch up after pulling: `alembic upgrade head`.
- Undo your last migration locally: `alembic downgrade -1`.
- Check that models and DB match: `alembic check`.
- Two PRs branching from the same parent create two heads (`alembic heads`).
  Rebase and regenerate yours on top, or run `alembic merge`.

## New (empty) database
```
alembic upgrade head
python main.py          # seeds model_registry + role rows
```

## Existing database created before Alembic (by `create_all()`)
Mark it as `0001`, **not** `head`. `stamp head` would record `0002` as applied
without creating its tables.

1. Back up: `pg_dump -Fc <db> > backup.dump`
2. Verify that the DB really matches the baseline. `create_all()` never altered
   existing tables, so an older DB can drift. Build a throwaway DB at `0001`,
   then compare the two schemas:
   ```
   createdb baseline_check
   DATABASE_URL=postgresql+psycopg2://<user>@<host>/baseline_check alembic upgrade 0001
   pg_dump --schema-only --no-owner <db>            > existing.sql
   pg_dump --schema-only --no-owner baseline_check  > baseline.sql
   diff existing.sql baseline.sql      # only alembic_version may differ
   dropdb baseline_check
   ```
   If the diff shows differences, fix the DB by hand first (or ask the team).
   Do not stamp a drifted DB.
3. `alembic stamp 0001`
4. `alembic upgrade head`
5. `alembic check` should print `No new upgrade operations detected.`

## Tests
- The suite uses `create_all()` on in-memory SQLite for speed. This happens in
  `tests/conftest.py`, and in `db.database.init_db()`, which only creates tables
  for an in-memory SQLite DB.
- `tests/test_migrations.py` runs the real migrations on a temp SQLite file and
  fails if they differ from `db/models_orm.py`. So a model change without a
  migration fails CI.
- `env.py` uses batch mode on SQLite, because SQLite cannot `ALTER` constraints.
