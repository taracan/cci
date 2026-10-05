"""Configuration for the Resume-JD Matching Portal.

This module loads configuration from multiple sources in order of priority:
1. AWS Secret Manager (if configured via AWS_SECRET_NAME and AWS_REGION)
2. Environment variables
3. Hardcoded defaults

AWS Secret Manager:
- Store your config as JSON in AWS Secret Manager
- Example secret content: {"SECRET_KEY": "abc123", "DATABASE_URL": "..."}
- Set AWS_SECRET_NAME env var to specify secret name (recommended: "resume-jd-portal/config")
- Set AWS_REGION env var to specify AWS region
- AWS is only contacted when both are set; AWS errors are logged and the app
  falls back to environment variables / defaults.

Environment Variables:
- All settings can be overridden via environment variables
- See individual settings for default values

Usage:
    from config import SECRET_KEY, DATABASE_URL, LLM_PROVIDER

Required Configuration:
    - SECRET_KEY: Application secret key (must be set in AWS or env)
    - ADMIN_KEY: Admin authentication key (must be set in AWS or env)
    - DATABASE_URL: Database connection string (must be set in AWS or env)
"""

import os
from pathlib import Path

from config_loader import Config, get_config

# Initialize config (non-strict mode for backward compatibility)
# Set strict_mode=True in production to fail fast on missing required config
_config = get_config()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RESUMES_DIR = DATA_DIR / "resumes"
PARSED_RESUMES_DIR = DATA_DIR / "parsed_resumes"
JDS_DIR = DATA_DIR / "jds"
CHROMA_DIR = DATA_DIR / "chroma"
LOG_DIR = BASE_DIR / os.getenv("LOG_DIR", _config.get_str("LOG_DIR", "logs"))

# Secrets — resolved from AWS Secret Manager → env var → safe default.
# The defaults are dev-only placeholders; real values come from AWS in
# production. Keeping a default (instead of required=True) means the app and
# the test suite never crash when AWS/env vars are absent (e.g. on CI).
SECRET_KEY = _config.get_str("SECRET_KEY", "dev-secret-key-change-in-production")
ADMIN_KEY = _config.get_str("ADMIN_KEY", "admin123")

# Database configuration
DATABASE_URL = _config.get_str(
    "DATABASE_URL",
    "postgresql+psycopg2://hemanthyegireddy@localhost:5432/resume_jd_portal",
)

# LLM configuration - not required (has defaults)
LLM_PROVIDER = _config.get_str("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = _config.get_str("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_LLM_MODEL = _config.get_str("DEFAULT_LLM_MODEL", "llama3.2:latest")
LLM_TIMEOUT_SECS = _config.get_int("LLM_TIMEOUT_SECS", 120)
LLM_TEMPERATURE = _config.get_float("LLM_TEMPERATURE", 0.2)

# Multi-tenant recruiter portal. Off = today's app, unchanged; all new
# multi-tenant behavior is gated behind this flag.
MULTI_TENANT = _config.get_bool("MULTI_TENANT", False)

# Matching pipeline
MATCH_TOP_K = _config.get_int("MATCH_TOP_K", 10)

# Queue manager
QUEUE_MAX_CONCURRENT = _config.get_int("QUEUE_MAX_CONCURRENT", 2)
QUEUE_TTL_SECS = _config.get_int("QUEUE_TTL_SECS", 180)

# --- AWS / S3 ---
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")

# --- Logging / observability ---
LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
LOG_BACKUP_COUNT = 5
LOG_LEVEL = _config.get_str("LOG_LEVEL", "INFO")
LOG_TO_CLOUDWATCH = _config.get_bool("LOG_TO_CLOUDWATCH", False)
CLOUDWATCH_LOG_GROUP = _config.get_str("CLOUDWATCH_LOG_GROUP", "resume-jd-portal")
CLOUDWATCH_LOG_STREAM = _config.get_str("CLOUDWATCH_LOG_STREAM", "")  # blank = host-app-date
CLOUDWATCH_REGION = _config.get_str("CLOUDWATCH_REGION", _config.get_str("AWS_REGION", ""))
CLOUDWATCH_RETENTION_DAYS = _config.get_int("CLOUDWATCH_RETENTION_DAYS", 30)
# Mask emails/names/resume text in the CloudWatch copy (local files stay full).
CLOUDWATCH_REDACT = _config.get_bool("CLOUDWATCH_REDACT", True)

# Create directories
for d in [RESUMES_DIR, PARSED_RESUMES_DIR, JDS_DIR, CHROMA_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)