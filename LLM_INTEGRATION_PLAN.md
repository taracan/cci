# Resume-JD Portal — LLM Integration Plan

> Goal: Add LLM "powers" to the resume parser + matching engine, with a **config switch**
> to choose between **Ollama** (local) and **AWS Bedrock** (cloud) as the backend.
> The existing rule-based logic stays as a fallback — nothing breaks if the LLM is offline.

---

## 1. Why Add LLMs — Current Weaknesses

| Area | Current (rule-based) | Problem | LLM Fix |
|---|---|---|---|
| Skill extraction | Fixed `KNOWN_SKILLS` keyword list | Misses skills not in the list (Keras, SpaCy, BeautifulSoup were skipped in the sample resume) | LLM reads the whole resume and extracts ALL skills, normalised |
| Experience | Regex on "N years" | Sample resume returned `0.0` years despite 2022–present history | LLM infers years from job date ranges |
| Skill matching | Exact set intersection | "Node.js" ≠ "NodeJS", "ML" ≠ "Machine Learning" | LLM does semantic matching |
| Match reason | Only a number | No explanation of *why* a candidate fits | LLM writes a human-readable summary + strengths/gaps |
| JD parsing | Admin types skills manually | Tedious, error-prone | LLM extracts required/preferred skills from raw JD text |

---

## 2. What the LLM Will Do (4 capabilities)

1. **Smart resume parsing** — extract skills, infer experience years, summarise the candidate profile from raw resume text.
2. **Smart JD parsing** — pull required/preferred skills + experience from raw JD text (helps admin auto-fill the upload form).
3. **Semantic match explanation** — given a parsed resume + JD, produce a match score with reasoning, matched strengths, and skill gaps.
4. **Fallback safety** — if the LLM is disabled or errors out, silently fall back to the existing rule-based functions.

---

## 3. The Config Switch (Ollama vs Bedrock)

A single environment-driven switch controls which backend is used.

```
LLM_PROVIDER = "ollama" | "bedrock" | "off"
```

- `ollama`  → calls a local Ollama server (default model e.g. `llama3.1` or `qwen2.5`)
- `bedrock` → calls AWS Bedrock (default model e.g. `anthropic.claude-3-sonnet`)
- `off`     → skip LLM entirely, use existing rule-based logic

New settings added to `config.py` (all overridable by env vars):

```python
LLM_PROVIDER      = os.getenv("LLM_PROVIDER", "off")        # off | ollama | bedrock
LLM_ENABLED       = LLM_PROVIDER != "off"

# Ollama
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL", "llama3.1")

# Bedrock
AWS_REGION        = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID  = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")

# Shared
LLM_TIMEOUT_SECS  = int(os.getenv("LLM_TIMEOUT_SECS", "60"))
LLM_TEMPERATURE   = float(os.getenv("LLM_TEMPERATURE", "0.2"))
```

---

## 4. Architecture — New `llm/` Package

A clean abstraction layer so routes never care which provider is active.

```
resume_jd_portal/
  llm/
    __init__.py
    base.py            # LLMClient abstract base class — one method: generate(prompt) -> str
    ollama_client.py   # OllamaClient(LLMClient)  — POST to /api/generate
    bedrock_client.py  # BedrockClient(LLMClient) — boto3 invoke_model
    factory.py         # get_llm_client() — reads LLM_PROVIDER, returns the right client (or None)
    prompts.py         # prompt templates: resume parse, JD parse, match explanation
    llm_service.py     # high-level functions used by the app (with rule-based fallback)
```

### Design principle: Strategy Pattern
- `base.LLMClient` defines the contract: `generate(prompt: str) -> str`
- `OllamaClient` and `BedrockClient` implement it differently
- `factory.get_llm_client()` picks one based on config
- Routes call `llm_service`, never the clients directly

This means: **adding a 3rd provider later (OpenAI, etc.) = one new file, zero route changes.**

### Data flow (resume upload example)
```
upload_resume route
      │
      ▼
llm_service.parse_resume_smart(text)
      │
      ├── if LLM_ENABLED:
      │       client = factory.get_llm_client()
      │       prompt = prompts.build_resume_parse_prompt(text)
      │       raw    = client.generate(prompt)
      │       data   = safe_json_parse(raw)   ← validated against expected keys
      │       if valid → return data
      │
      └── else / on error:
              return resume_parser.parse_resume(text)   ← existing rule-based fallback
```

---

## 5. Output Contracts (what the LLM must return)

All LLM calls return **strict JSON** we can parse. If parsing fails → fallback.

### 5.1 Smart Resume Parse
```json
{
  "skills": ["Python", "PyTorch", "SpaCy", "LangChain", "..."],
  "experience_years": 3.0,
  "education": "Masters",
  "summary": "ML engineer with NLP and LLM fine-tuning experience...",
  "top_strengths": ["LLM fine-tuning", "RAG pipelines", "NER modeling"]
}
```
Backwards compatible: keeps `skills`, `experience_years`, `education` (adds `summary`, `top_strengths`).

### 5.2 Smart JD Parse
```json
{
  "required_skills": ["Python", "FastAPI", "PostgreSQL"],
  "preferred_skills": ["Redis", "AWS"],
  "experience_years": 3,
  "role_title": "Python Backend Developer"
}
```

### 5.3 Semantic Match Explanation
```json
{
  "overall_score": 82.5,
  "matched_skills": ["Python", "FastAPI", "Docker"],
  "missing_skills": ["Kubernetes"],
  "reasoning": "Strong backend fit. Candidate has FastAPI + PostgreSQL...",
  "verdict": "Strong Match"
}
```

---

## 6. Implementation Steps (order)

1. **`config.py`** — add the LLM settings block (Section 3).
2. **`llm/base.py`** — `LLMClient` ABC with `generate()`.
3. **`llm/ollama_client.py`** — `requests.post` to Ollama `/api/generate`, JSON format mode.
4. **`llm/bedrock_client.py`** — `boto3` Bedrock `invoke_model` wrapper.
5. **`llm/factory.py`** — `get_llm_client()` switch on `LLM_PROVIDER`.
6. **`llm/prompts.py`** — 3 prompt builders (resume, JD, match).
7. **`llm/llm_service.py`** — `parse_resume_smart()`, `parse_jd_smart()`, `explain_match()` with fallback + `safe_json_parse()`.
8. **Wire into routes**:
   - `routes/candidate.py` → `upload_resume` uses `parse_resume_smart`; `match_detail` adds `explain_match`.
   - `routes/admin.py` → `upload_jd` optionally auto-fills skills via `parse_jd_smart`.
9. **`requirements.txt`** — add `requests`, `boto3` (pdfplumber already there).
10. **Templates** — show LLM summary + reasoning when present (graceful if absent).
11. **Test** — `LLM_PROVIDER=off` (rule-based), then `ollama`, then `bedrock`.

---

## 7. Safety & Fallback Rules

- LLM never blocks the app: every call wrapped in try/except → rule-based fallback.
- All LLM JSON output validated against required keys before use.
- Timeouts on every call (`LLM_TIMEOUT_SECS`) so a hung Ollama/Bedrock can't freeze uploads.
- No credentials hardcoded — Bedrock uses the standard AWS credential chain; Ollama needs no auth.
- Default `LLM_PROVIDER=off` so the app behaves exactly as today until explicitly switched on.

---

## 8. How to Run Each Mode

```bash
# Rule-based (current behaviour, default)
python main.py

# Local LLM via Ollama
ollama pull llama3.1
LLM_PROVIDER=ollama OLLAMA_MODEL=llama3.1 python main.py

# AWS Bedrock
LLM_PROVIDER=bedrock BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0 python main.py
```

---

## 9. Open Questions (confirm before implementing)

1. **Ollama model** — which model do you have pulled? (`llama3.1`, `qwen2.5`, `mistral`?) I'll set it as default.
2. **Bedrock** — do you have AWS credentials configured locally + Bedrock model access enabled?
3. **Scope for round 1** — implement all 3 capabilities (resume + JD + match), or start with just **smart resume parsing** and expand after?
4. **Match scoring** — should the LLM fully replace `matching_engine.py`, or produce the *explanation* on top of the existing numeric score? (I recommend: keep numeric engine, add LLM reasoning layer.)
```
