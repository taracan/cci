"""Configuration for the Resume-JD Matching Portal."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RESUMES_DIR = DATA_DIR / "resumes"
PARSED_RESUMES_DIR = DATA_DIR / "parsed_resumes"
JDS_DIR = DATA_DIR / "jds"
CHROMA_DIR = DATA_DIR / "chroma"
LOG_DIR = BASE_DIR / os.getenv("LOG_DIR", "logs")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ADMIN_KEY = os.getenv("ADMIN_KEY", "admin123")

# --- Database (SQLAlchemy) — PostgreSQL. Override via DATABASE_URL env var. ---
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://hemanthyegireddy@localhost:5432/resume_jd_portal",
)

# --- LLM (Ollama local only for now) ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "llama3.2:latest")
LLM_TIMEOUT_SECS = int(os.getenv("LLM_TIMEOUT_SECS", "120"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# --- Matching pipeline ---
MATCH_TOP_K = int(os.getenv("MATCH_TOP_K", "10"))

# --- Queue manager ---
QUEUE_MAX_CONCURRENT = int(os.getenv("QUEUE_MAX_CONCURRENT", "2"))
QUEUE_TTL_SECS = int(os.getenv("QUEUE_TTL_SECS", "180"))

# --- Logging / observability ---
LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
LOG_BACKUP_COUNT = 5
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_TO_CLOUDWATCH = os.getenv("LOG_TO_CLOUDWATCH", "false").lower() == "true"
CLOUDWATCH_LOG_GROUP = os.getenv("CLOUDWATCH_LOG_GROUP", "resume-jd-portal")
CLOUDWATCH_LOG_STREAM = os.getenv("CLOUDWATCH_LOG_STREAM", "")  # blank = host-app-date
CLOUDWATCH_REGION = os.getenv("CLOUDWATCH_REGION", os.getenv("AWS_REGION", ""))
CLOUDWATCH_RETENTION_DAYS = int(os.getenv("CLOUDWATCH_RETENTION_DAYS", "30"))
# Mask emails/names/resume text in the CloudWatch copy (local files stay full).
CLOUDWATCH_REDACT = os.getenv("CLOUDWATCH_REDACT", "true").lower() == "true"

# Create directories
for d in [RESUMES_DIR, PARSED_RESUMES_DIR, JDS_DIR, CHROMA_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
