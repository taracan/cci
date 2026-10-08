"""Unit tests for the AWS Secret Manager helper.

These tests do NOT require real AWS access or credentials — they verify the
safe-fallback behaviour, which is what runs on CI (where AWS is unavailable).
Placed under tests/ so pytest discovers it.
"""
import os
import json
import importlib
from pathlib import Path

import pytest

import aws_secret_manager as asm
import config_loader
from config_loader import Config

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- helpers ---

def _fake_session(monkeypatch, *, raises=None, secret_string=None, calls=None):
    """Replace boto3.Session so no real AWS call can happen."""
    class _Client:
        def get_secret_value(self, SecretId):
            if calls is not None:
                calls.append(SecretId)
            if raises is not None:
                raise raises
            return {"SecretString": secret_string}

    class _Session:
        def client(self, service_name, region_name):
            return _Client()

    monkeypatch.setattr(asm.boto3, "Session", _Session)


def _forbid_aws(monkeypatch):
    """Make any attempt to contact AWS fail the test."""
    def _boom(*a, **k):
        raise AssertionError("AWS must not be contacted")
    if asm.BOTO3_AVAILABLE:
        monkeypatch.setattr(asm.boto3, "Session", _boom)
    monkeypatch.setattr(config_loader, "get_secret", _boom)


@pytest.fixture
def no_aws_env(monkeypatch):
    for var in ("AWS_SECRET_NAME", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------- Fix 2: AWS is opt-in only ------

def test_get_secret_returns_none_without_region(monkeypatch, no_aws_env):
    """With no region configured, get_secret must return None WITHOUT calling AWS."""
    if asm.BOTO3_AVAILABLE:
        monkeypatch.setattr(asm.boto3, "Session",
                            lambda *a, **k: pytest.fail("AWS must not be contacted"))
    result = asm.get_secret("SomeSecretThatDoesNotExist", region_name=None)
    assert result is None


def test_config_does_not_contact_aws_without_secret_name(monkeypatch, no_aws_env):
    """No AWS_SECRET_NAME -> Config never calls AWS, even if a region is set."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    _forbid_aws(monkeypatch)
    cfg = Config()
    assert cfg.secret_name is None
    assert cfg._aws_secret_cache is None


def test_config_uses_aws_when_secret_name_set(monkeypatch, no_aws_env):
    """AWS_SECRET_NAME set -> Config fetches that exact secret and prefers it over env."""
    monkeypatch.setenv("AWS_SECRET_NAME", "resume-jd-portal/config")
    monkeypatch.setenv("SECRET_KEY", "from-env")
    requested = []
    monkeypatch.setattr(config_loader, "get_secret",
                        lambda name, *a, **k: requested.append(name) or {"SECRET_KEY": "from-aws"})
    cfg = Config()
    assert requested == ["resume-jd-portal/config"]
    assert cfg.get_str("SECRET_KEY") == "from-aws"


# ------------------------------- Fix 1: AWS failures never crash startup ----

@pytest.mark.parametrize("error_code", ["ResourceNotFoundException", "AccessDeniedException"])
def test_get_secret_returns_none_on_client_error(monkeypatch, error_code):
    """ClientError (secret missing / access denied) -> None, not an exception."""
    botocore_exc = pytest.importorskip("botocore.exceptions")
    err = botocore_exc.ClientError({"Error": {"Code": error_code, "Message": "x"}},
                                   "GetSecretValue")
    _fake_session(monkeypatch, raises=err)
    assert asm.get_secret("resume-jd-portal/config", region_name="us-east-1") is None


@pytest.mark.parametrize("make_error", [
    lambda exc: exc.NoCredentialsError(),
    lambda exc: exc.EndpointConnectionError(endpoint_url="https://example.invalid"),
])
def test_get_secret_returns_none_on_botocore_error(monkeypatch, make_error):
    """No credentials / network errors -> None, not an exception."""
    botocore_exc = pytest.importorskip("botocore.exceptions")
    _fake_session(monkeypatch, raises=make_error(botocore_exc))
    assert asm.get_secret("resume-jd-portal/config", region_name="us-east-1") is None


def test_get_secret_returns_none_on_invalid_json(monkeypatch):
    pytest.importorskip("botocore")
    _fake_session(monkeypatch, secret_string="{not json")
    assert asm.get_secret("resume-jd-portal/config", region_name="us-east-1") is None


def test_get_secret_parses_valid_json(monkeypatch):
    pytest.importorskip("botocore")
    _fake_session(monkeypatch, secret_string=json.dumps({"SECRET_KEY": "abc"}))
    assert asm.get_secret("resume-jd-portal/config", region_name="us-east-1") == {"SECRET_KEY": "abc"}


def test_config_falls_back_to_env_when_aws_fails(monkeypatch, no_aws_env):
    """Configured-but-broken AWS -> Config loads and uses env vars / defaults."""
    botocore_exc = pytest.importorskip("botocore.exceptions")
    monkeypatch.setenv("AWS_SECRET_NAME", "resume-jd-portal/config")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SECRET_KEY", "from-env")
    err = botocore_exc.ClientError({"Error": {"Code": "ResourceNotFoundException"}},
                                   "GetSecretValue")
    _fake_session(monkeypatch, raises=err)
    cfg = Config()
    assert cfg._aws_secret_cache is None
    assert cfg.get_str("SECRET_KEY") == "from-env"
    assert cfg.get_str("NOT_SET_ANYWHERE", "default") == "default"


# ------------------------- Fix 3: string values are preserved exactly -------

@pytest.mark.parametrize("raw", ["00123", "007", "1e3", "123", "0.10", "nan", "inf", " 42 "])
def test_get_str_preserves_env_value_exactly(monkeypatch, no_aws_env, raw):
    monkeypatch.setenv("SECRET_KEY", raw)
    assert Config().get_str("SECRET_KEY") == raw


@pytest.mark.parametrize("raw", ["00123", "1e3", "0.10"])
def test_get_str_preserves_aws_value_exactly(monkeypatch, no_aws_env, raw):
    monkeypatch.setenv("AWS_SECRET_NAME", "resume-jd-portal/config")
    monkeypatch.setattr(config_loader, "get_secret", lambda *a, **k: {"ADMIN_KEY": raw})
    assert Config().get_str("ADMIN_KEY") == raw


def test_typed_getters_still_convert(monkeypatch, no_aws_env):
    """Backward compat: int/float/bool getters and get() still convert values."""
    monkeypatch.setenv("MATCH_TOP_K", "010")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    monkeypatch.setenv("LOG_TO_CLOUDWATCH", "true")
    cfg = Config()
    assert cfg.get_int("MATCH_TOP_K", 5) == 10
    assert cfg.get_float("LLM_TEMPERATURE", 0.0) == 0.2
    assert cfg.get_bool("LOG_TO_CLOUDWATCH", False) is True
    assert cfg.get("MATCH_TOP_K") == 10
    assert cfg.get_str("MISSING_KEY", "fallback") == "fallback"


def test_required_missing_still_raises(monkeypatch, no_aws_env):
    monkeypatch.delenv("DEFINITELY_MISSING_KEY", raising=False)
    with pytest.raises(ValueError, match="DEFINITELY_MISSING_KEY"):
        Config().get_required_str("DEFINITELY_MISSING_KEY")


def test_get_secret_returns_none_when_boto3_missing(monkeypatch):
    """If boto3 isn't available, get_secret must fall back to None, not crash."""
    monkeypatch.setattr(asm, "BOTO3_AVAILABLE", False)
    result = asm.get_secret("AnySecret", region_name="us-east-1")
    assert result is None


def test_get_secret_string_returns_none_for_missing_secret(monkeypatch):
    """get_secret_string returns None when the underlying secret is unavailable."""
    monkeypatch.setattr(asm, "get_secret", lambda *a, **k: None)
    assert asm.get_secret_string("AnySecret", key="SECRET_KEY") is None


def test_get_secret_string_extracts_key():
    """get_secret_string should pull a single key out of a secret dict."""
    # Patch get_secret to return a fake bundle, so no AWS call happens.
    fake = {"SECRET_KEY": "abc123", "ADMIN_KEY": "admin"}
    original = asm.get_secret
    asm.get_secret = lambda *a, **k: fake
    try:
        assert asm.get_secret_string("cfg", key="SECRET_KEY") == "abc123"
        assert asm.get_secret_string("cfg", key="ADMIN_KEY") == "admin"
    finally:
        asm.get_secret = original


def test_default_secret_name_is_defined():
    """The module should expose a default secret name constant."""
    assert isinstance(asm.DEFAULT_SECRET_NAME, str)
    assert asm.DEFAULT_SECRET_NAME != ""


# ------------------------ Fix 4: one secret name everywhere -----------------

def test_default_secret_name_value():
    assert asm.DEFAULT_SECRET_NAME == "resume-jd-portal/config"


@pytest.mark.parametrize("path", ["env.example", "AWS_SECRET_MANAGER_SETUP.md", "config.py"])
def test_default_secret_name_consistent_across_files(path):
    """Docs/examples must use the same secret name as the code, and no stale variant."""
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert asm.DEFAULT_SECRET_NAME in text
    assert "RESUME_JD_PORTAL/Config" not in text
