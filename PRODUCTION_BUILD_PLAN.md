# Resume-JD Portal — Production Build Plan (Staged)

> Ollama-only LLM. Embeddings-first matching → LLM on shortlist. Full tracing to
> rotating local files + DB now; CloudWatch/Grafana behind a flag for later.
> DB via SQLAlchemy: SQLite now → Postgres after code changes (one URL change).

---

## Locked Decisions

| Topic | Decision |
|---|---|
| LLM backend | Ollama local only (`llama3.2`, `llama3`, `deepseek-r1` selectable) |
| Embedding model | all-MiniLM-L6-v2 (ChromaDB) — unchanged |
| Match flow | Stage 1 embeddings rank ALL → top-K; Stage 2 LLM on top-K only |
| DB | SQLAlchemy → SQLite now, Postgres later (swap `DATABASE_URL`) |
| Model registry | DB table; every request checks status = active before use |
| Model selection | Admin/user picks model in UI (speed vs quality) |
| Queue manager | Max concurrent requests + per-request TTL |
| Logging | Rotating JSON files (10MB × 5) + DB rows; CloudWatch flag off for now |
| Tracing | trace_id per request, span_id per stage, request/response bodies stored |
| UI | Horizontal menu, logo animation, account section, users & roles, model picker |

---

## Target Structure

```
resume_jd_portal/
  config.py                     # + LLM, DB, queue, logging settings
  db/
    __init__.py
    database.py                 # SQLAlchemy engine/session (SQLite→Postgres)
    models_orm.py               # ORM: ModelRegistry, LlmCall, RequestTrace, User, Role
    seed.py                     # seed model registry rows (ollama models + status)
  observability/
    __init__.py
    logging_config.py           # JSON formatter, RotatingFileHandler(10MB×5), console
    tracing.py                  # trace_id/span_id context, @span timer decorator
    cloudwatch.py               # stub handler, enabled by env flag (later)
  llm/
    __init__.py
    base.py                     # LLMClient ABC
    ollama_client.py            # POST http://localhost:11434/api/generate
    factory.py                  # get_llm_client(model_id) + registry status check
    prompts.py                  # resume-parse / jd-parse / match-explain
    llm_service.py              # parse_resume_smart / parse_jd_smart / explain_match
    queue_manager.py            # bounded concurrency + TTL
  pipeline/
    matcher.py                  # two_stage_match(): embeddings shortlist → LLM
  matching_engine.py            # keyword scoring (kept as signal + fallback)
  vector_matcher.py             # embeddings (exists)
  resume_parser.py              # rule-based (kept as fallback only)
  routes/...                    # wired to pipeline; + model-select, users/roles admin
  templates/...                 # new base layout, menu, account, model picker
```

---

## DB Schema (SQLAlchemy models)

**model_registry**
- id, model_id (e.g. `llama3.2:latest`), family (`llama`,`deepseek`), provider (`ollama`)
- display_name, purpose (`reasoning`|`parsing`|`embedding`)
- status (`active`|`inactive`|`retired`), is_default (bool)
- max_tokens, notes, created_at, updated_at

**llm_call**  (one row per LLM invocation)
- id, trace_id, span_id, model_id, family, provider
- task (`parse_resume`|`parse_jd`|`explain_match`)
- request_body (JSON text), response_body (JSON text)
- prompt_tokens, completion_tokens, latency_ms
- status (`ok`|`error`|`timeout`|`rejected`), error_msg, created_at

**request_trace**  (one row per HTTP request)
- id, trace_id, route, method, user_id, role
- started_at, ended_at, total_latency_ms, status_code
- stage_summary (JSON: which spans ran + timings)

**users / roles**  (RBAC)
- user: id, name, email, password_hash, role_id, is_active, created_at
- role: id, name (`admin`|`recruiter`|`candidate`), permissions (JSON)

---

## Request Lifecycle (what gets logged)

```
HTTP request
  → make trace_id, open request_trace row               [LOG: request.received]
  → auth: user_id + role                                [LOG: auth.ok / auth.denied]
  → resolve model: read model_registry
        if status != active → reject 409                [LOG: model.rejected]        [DB: llm_call status=rejected]
  → queue_manager.acquire(ttl)                          [LOG: queue.enqueued/started/timeout]
  → STAGE 1 embeddings (span)                           [LOG: match.embed.start/end + scores]
        rank all candidates → top-K
  → STAGE 2 for each top-K (span)                       [LOG: llm.call.start/end + latency + tokens]
        llm_service.explain_match(model_id)             [DB: llm_call row per call]
  → assemble result                                     [LOG: match.result + final scores]
  → close request_trace row                             [LOG: request.completed + total latency]  [DB: request_trace]
  (any error/timeout logged with trace_id + span_id, request_trace status set)
```

---

## Build Stages (verify each before next)

**Stage 1 — Observability + DB foundation**
- `db/` (SQLAlchemy, SQLite), `observability/` (JSON rotating logs, trace/span).
- Seed model_registry with the installed Ollama models (status=active, `llama3.2` default).
- Verify: app imports, DB file created, tables + seed rows present, a test log line written.

**Stage 2 — LLM layer + queue + model-status gate**
- `llm/` (ollama_client, factory with registry status check, prompts, llm_service, queue_manager).
- Every call logged + written to llm_call. Rejects inactive/retired models.
- Verify: `parse_resume_smart` on a sample returns JSON; llm_call row written; inactive model → rejected.

**Stage 3 — Two-stage matcher**
- `pipeline/matcher.py`: embeddings shortlist → LLM explain on top-K.
- Wire into candidate + admin routes; full lifecycle logging.
- Verify: admin "View Candidates" runs end-to-end, trace shows both stages.

**Stage 4 — UI + RBAC**
- New base layout (horizontal menu, logo animation, account section), model picker,
  users & roles admin pages.
- Verify: pages render, model selection persists per request, roles enforced.

**Stage 5 — Postgres switch + CloudWatch flag**
- Install Postgres, change `DATABASE_URL`, run migrations.
- Enable CloudWatch handler via env when AWS is ready → Grafana.

---

## Config additions (config.py)

```python
DATABASE_URL      = os.getenv("DATABASE_URL", "sqlite:///data/app.db")  # → postgresql://... later
LLM_PROVIDER      = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "llama3.2:latest")
LLM_TIMEOUT_SECS  = int(os.getenv("LLM_TIMEOUT_SECS", "120"))
MATCH_TOP_K       = int(os.getenv("MATCH_TOP_K", "10"))
QUEUE_MAX_CONCURRENT = int(os.getenv("QUEUE_MAX_CONCURRENT", "2"))
QUEUE_TTL_SECS       = int(os.getenv("QUEUE_TTL_SECS", "180"))
LOG_DIR           = os.getenv("LOG_DIR", "logs")
LOG_MAX_BYTES     = 10 * 1024 * 1024   # 10 MB
LOG_BACKUP_COUNT  = 5
LOG_TO_CLOUDWATCH = os.getenv("LOG_TO_CLOUDWATCH", "false") == "true"
```
