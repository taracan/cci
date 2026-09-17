# Resume-JD Portal — Production Hybrid AI Architecture

> **Goal:** Combine **embeddings + vector DB** (fast, cheap, scalable retrieval/ranking)
> with **LLMs** (deep reasoning, extraction, explanation) so each is used only where it
> is the right tool. Keep the existing rule-based logic as a guaranteed fallback.

---

## 1. Core Principle — Which Tool for Which Job

| Need | Use | Why |
|---|---|---|
| "Which of 10,000 resumes are even worth looking at for this JD?" | **Embeddings + Vector DB** | Milliseconds, ~$0, scales to millions. Cheap first-pass filter. |
| "Extract skills / years / education from messy resume text" | **LLM** | Understands context, dates, synonyms. Keyword lists miss things. |
| "Turn a raw JD paragraph into structured required/preferred skills" | **LLM** | One-off per JD, needs comprehension. |
| "Score + explain WHY this candidate fits (strengths, gaps)" | **LLM** | Needs reasoning + human-readable output. |
| "Rank the shortlist precisely" | **Embeddings (rerank) → LLM (top N only)** | Vector ranks cheaply; LLM refines only the finalists. |
| "Deduplicate / find similar past candidates" | **Embeddings + Vector DB** | Similarity is exactly what vectors do. |

**Golden rule:** *Embeddings narrow the field cheaply. LLMs do deep work on the few that survive.*
Never run an LLM over all candidates — run it only on the vector-shortlisted top-K.

---

## 2. The Two-Stage Funnel (heart of the design)

```
                 ALL candidates (could be thousands)
                              │
        ┌─────────────────────▼─────────────────────┐
        │  STAGE 1 — RETRIEVE & RANK  (Embeddings)   │
        │  - Embed resume + JD with all-MiniLM-L6-v2 │
        │  - Vector similarity in ChromaDB           │
        │  - Cheap keyword skill/exp/role score      │
        │  - Blend → shortlist top-K (e.g. K=10)     │
        └─────────────────────┬─────────────────────┘
                              │  only top-K continue
        ┌─────────────────────▼─────────────────────┐
        │  STAGE 2 — REASON & EXPLAIN  (LLM)         │
        │  - For each of top-K: LLM match analysis   │
        │  - Strengths, gaps, verdict, reasoning     │
        │  - Structured JSON output                  │
        └─────────────────────┬─────────────────────┘
                              │
                    Final ranked list + explanations
```

**Cost impact:** If you have 1,000 candidates and K=10, you run **10** LLM calls instead of
1,000 — a 100x cost/latency reduction with almost no quality loss, because the vector stage
already removed the obviously-irrelevant 990.

---

## 3. Where Each Piece Lives in the Pipeline

### 3.1 Resume Upload (per candidate, once)
```
PDF → extract text
  ├── LLM: parse_resume_smart()   → skills, years, education, summary, strengths   [LLM]
  │       (fallback: rule-based resume_parser.py if LLM off/fails)
  ├── Embeddings: embed(resume_text) → store vector in ChromaDB (id=candidate_id)  [VECTOR]
  └── Save parsed JSON to disk
```

### 3.2 JD Upload (per JD, once)
```
Raw JD text → LLM: parse_jd_smart() → required/preferred skills, exp, title        [LLM]
                                     (auto-fills the admin form; admin can edit)
           → Embeddings: embed(jd_text) → optional JD vector cache                 [VECTOR]
```

### 3.3 Matching (candidate views jobs OR admin ranks candidates)
```
STAGE 1  [VECTOR + rule-based]  — runs for EVERY candidate/JD, cheap
  semantic_score = cosine(resume_vec, jd_vec)
  keyword_score  = skills/exp/role  (existing matching_engine)
  blended        = 0.4*skill + 0.35*semantic + 0.15*exp + 0.10*role
  → sort, take top-K

STAGE 2  [LLM]  — runs ONLY for top-K
  explain_match(resume, jd) → { score, matched, gaps, reasoning, verdict }
  → present to user with explanation
```

---

## 4. LLM Provider Strategy — Two LLMs, Clear Roles

You asked to use **both** LLMs. Use them by *job type*, not randomly.

| Provider | Use it for | Why |
|---|---|---|
| **Ollama (local)** | Dev, bulk/batch parsing, cost-sensitive high-volume tasks, PII-sensitive data that must not leave the machine | Free, private, no per-token cost, unlimited calls |
| **AWS Bedrock (cloud, Claude)** | Production match explanations, final candidate reasoning, anything user-facing where quality matters most | Higher quality, managed, scalable, no local GPU needed |

**Recommended routing:**
- `parse_resume_smart` (high volume, runs on every upload) → **Ollama** (cheap, private — resumes are PII)
- `parse_jd_smart` (low volume, admin convenience) → **Ollama** or **Bedrock** (either is fine)
- `explain_match` (user-facing, quality-critical, low volume after vector filter) → **Bedrock**

This is controlled by config, so you can flip any task to any provider:
```python
LLM_PROVIDER          = "ollama" | "bedrock" | "off"   # global default
LLM_PROVIDER_PARSING  = os.getenv("LLM_PROVIDER_PARSING", LLM_PROVIDER)   # resume/JD parse
LLM_PROVIDER_REASONING= os.getenv("LLM_PROVIDER_REASONING", LLM_PROVIDER) # match explain
```
`off` anywhere → that task silently falls back to rule-based / vector-only.

---

## 5. Target Architecture (packages)

```
resume_jd_portal/
  config.py                 # + LLM settings, provider routing, thresholds
  matching_engine.py        # keyword scoring (exists)
  vector_matcher.py         # embeddings + ChromaDB (exists)
  resume_parser.py          # rule-based parsing (exists — becomes fallback)

  llm/                      # NEW — LLM abstraction layer
    __init__.py
    base.py                 # LLMClient ABC: generate(prompt) -> str
    ollama_client.py        # local Ollama backend
    bedrock_client.py       # AWS Bedrock backend
    factory.py              # get_llm_client(task) -> client | None  (reads routing)
    prompts.py              # resume-parse, jd-parse, match-explain templates
    llm_service.py          # parse_resume_smart / parse_jd_smart / explain_match
                            #   each: try LLM → validate JSON → fallback

  pipeline/                 # NEW — orchestration (the funnel)
    matcher.py              # two_stage_match(): vector shortlist → LLM explain
```

**Design rule:** routes call `pipeline` / `llm_service` — never the clients directly.
Adding a 3rd provider (OpenAI) later = one new file, zero route changes (Strategy pattern).

---

## 6. Reliability & Production Concerns

| Concern | Approach |
|---|---|
| LLM down / slow | Timeout on every call (`LLM_TIMEOUT_SECS`); on failure → vector + rule-based result still returned |
| Bad LLM JSON | `safe_json_parse()` validates required keys; invalid → fallback |
| Cost blowup | LLM only on top-K after vector filter; cache LLM results per (resume_id, jd_id) |
| Latency (user-facing) | Vector stage is instant; run LLM explanation async or only on the detail page, not the list |
| Embedding model load time | Load once, cache singleton (already done in `vector_matcher.py`) |
| PII leaving machine | Route resume parsing to local Ollama; only send minimal text to Bedrock |
| Vector store persistence | ChromaDB `PersistentClient` on disk (dev) → managed vector DB (Pinecone/pgvector) in prod |
| Reproducibility | `LLM_TEMPERATURE=0.2` for extraction; pin model IDs |

---

## 7. Caching Strategy (big cost saver)

```
LLM match explanation cache key = hash(resume_id + jd_id + resume_version + jd_version)
```
- If resume and JD unchanged → return cached explanation, **zero LLM cost**.
- Invalidate when a resume is re-uploaded or a JD is edited.
- Store in a small `data/llm_cache.json` (dev) → Redis (prod).

---

## 8. Implementation Phases

**Phase 1 — Vector foundation (DONE)**
- `vector_matcher.py`, blended scoring, ChromaDB persistence, backfill script. ✅

**Phase 2 — LLM abstraction layer**
- Build `llm/` package: base, ollama_client, bedrock_client, factory, prompts, llm_service.
- Config switch + per-task provider routing. Default `off` → nothing changes.

**Phase 3 — Smart parsing**
- Wire `parse_resume_smart` into resume upload (Ollama).
- Wire `parse_jd_smart` into JD upload to auto-fill the admin form.

**Phase 4 — Two-stage matcher**
- Build `pipeline/matcher.py`: vector shortlist (top-K) → LLM `explain_match` on finalists (Bedrock).
- Show reasoning/strengths/gaps in match_detail + ranked_candidates templates.

**Phase 5 — Production hardening**
- LLM result caching, async explanation, metrics (latency, cost/call, cache hit rate).
- Swap ChromaDB → pgvector/Pinecone if scale demands it.

---

## 9. Decision Cheat-Sheet (memorize this)

- **Comparing / searching / ranking many items?** → Embeddings + Vector DB.
- **Extracting structured data from free text?** → LLM.
- **Generating human-readable reasoning/feedback?** → LLM.
- **High volume + cost/privacy sensitive?** → local LLM (Ollama).
- **Low volume + quality critical + user-facing?** → cloud LLM (Bedrock).
- **Always** put embeddings first to shrink the set, then LLM on the survivors.

---

## 10. Open Questions (confirm before Phase 2 build)

1. Ollama: which model do you have pulled (`llama3.1`, `qwen2.5`, `mistral`)?
2. Bedrock: AWS creds + model access enabled locally?
3. Confirm routing: resume/JD parse → Ollama, match explanation → Bedrock?
4. Top-K for the LLM stage — start with K=10?
