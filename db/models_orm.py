"""SQLAlchemy ORM models: model registry, LLM call log, request trace, RBAC."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Integer, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


class ModelRegistry(Base):
    """Registry of every model the platform can use and its lifecycle status."""

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    family: Mapped[str] = mapped_column(String(64))          # llama | deepseek | minilm
    provider: Mapped[str] = mapped_column(String(32), default="ollama")
    display_name: Mapped[str] = mapped_column(String(128), default="")
    purpose: Mapped[str] = mapped_column(String(32), default="reasoning")  # reasoning|parsing|embedding
    status: Mapped[str] = mapped_column(String(16), default="active")      # active|inactive|retired
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ModelRegistry {self.model_id} status={self.status}>"


class LlmCall(Base):
    """One row per LLM invocation — full request/response for observability."""

    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    span_id: Mapped[str] = mapped_column(String(36), index=True)
    model_id: Mapped[str] = mapped_column(String(128), index=True)
    family: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(32), default="ollama")
    task: Mapped[str] = mapped_column(String(32), default="")   # parse_resume|parse_jd|explain_match
    request_body: Mapped[str] = mapped_column(Text, default="")
    response_body: Mapped[str] = mapped_column(Text, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok|error|timeout|rejected
    error_msg: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<LlmCall {self.task} model={self.model_id} status={self.status}>"


class RequestTrace(Base):
    """One row per HTTP request — end-to-end lifecycle summary."""

    __tablename__ = "request_trace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    route: Mapped[str] = mapped_column(String(256), default="")
    method: Mapped[str] = mapped_column(String(8), default="")
    user_id: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    total_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    stage_summary: Mapped[str] = mapped_column(Text, default="")   # JSON: spans + timings

    def __repr__(self) -> str:
        return f"<RequestTrace {self.method} {self.route} trace={self.trace_id}>"


class Role(Base):
    """RBAC role with a JSON permissions blob."""

    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)   # admin|recruiter|candidate
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    users: Mapped[list["UserAccount"]] = relationship(back_populates="role")

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class UserAccount(Base):
    """Platform user linked to a role (RBAC)."""

    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    role: Mapped["Role"] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<UserAccount {self.email} active={self.is_active}>"
