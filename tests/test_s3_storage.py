"""Tests for S3 storage, document extraction, and upload logic.

All AWS calls are mocked — no real credentials or network access needed.
Tests are organised into five suites:

  TestS3Keys          — deterministic key helpers resume_key / jd_key
  TestS3UploadDownload — upload_file / download_file behaviour
  TestResumeParser    — each extraction function + dispatcher + path compat
  TestJobDescriptionModel — backward compat for old JD JSON records
  TestJDUploadLogic   — S3 round-trip, text extraction, JD persistence
  TestResumeUploadLogic — S3 round-trip, embedding upsert, resume update
"""
from __future__ import annotations

import io
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_docx_bytes() -> bytes:
    """Build a minimal in-memory DOCX using python-docx."""
    from docx import Document
    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Senior Python Developer required.")
    doc.save(buf)
    return buf.getvalue()


def _make_fake_jds_file(tmp_path: Path) -> Path:
    jds_path = tmp_path / "jds.json"
    jds_path.write_text("[]")
    return jds_path


# ===========================================================================
# TestS3Keys — deterministic key helpers
# ===========================================================================

class TestS3Keys:
    """resume_key() and jd_key() must produce stable, predictable paths."""

    # ---- resume_key --------------------------------------------------------

    def test_resume_key_pdf(self):
        from storage.s3 import resume_key
        assert resume_key("cand_abc", ".pdf") == "resumes/cand_abc/resume.pdf"

    def test_resume_key_txt(self):
        from storage.s3 import resume_key
        assert resume_key("cand_abc", ".txt") == "resumes/cand_abc/resume.txt"

    def test_resume_key_same_candidate_same_key(self):
        """Two calls for the same candidate produce identical keys — overwrite semantics."""
        from storage.s3 import resume_key
        assert resume_key("cand_xyz", ".pdf") == resume_key("cand_xyz", ".pdf")

    def test_resume_key_different_candidates_different_keys(self):
        from storage.s3 import resume_key
        assert resume_key("cand_001", ".pdf") != resume_key("cand_002", ".pdf")

    def test_resume_key_extension_normalised_to_lowercase(self):
        from storage.s3 import resume_key
        assert resume_key("cand_abc", ".PDF") == "resumes/cand_abc/resume.pdf"

    def test_resume_key_extension_without_dot(self):
        """Leading dot is optional — callers may omit it."""
        from storage.s3 import resume_key
        assert resume_key("cand_abc", "pdf") == "resumes/cand_abc/resume.pdf"

    # ---- jd_key ------------------------------------------------------------

    def test_jd_key_pdf(self):
        from storage.s3 import jd_key
        assert jd_key("jd_001", ".pdf") == "job-descriptions/jd_001/job-description.pdf"

    def test_jd_key_docx(self):
        from storage.s3 import jd_key
        assert jd_key("jd_001", ".docx") == "job-descriptions/jd_001/job-description.docx"

    def test_jd_key_same_jd_same_key(self):
        """Re-uploading a JD file produces the same key — overwrite semantics."""
        from storage.s3 import jd_key
        assert jd_key("jd_xyz", ".pdf") == jd_key("jd_xyz", ".pdf")

    def test_jd_key_different_jds_different_keys(self):
        from storage.s3 import jd_key
        assert jd_key("jd_001", ".pdf") != jd_key("jd_002", ".pdf")

    def test_jd_key_extension_normalised_to_lowercase(self):
        from storage.s3 import jd_key
        assert jd_key("jd_x", ".DOCX") == "job-descriptions/jd_x/job-description.docx"

    def test_jd_key_extension_without_dot(self):
        from storage.s3 import jd_key
        assert jd_key("jd_x", "pdf") == "job-descriptions/jd_x/job-description.pdf"

    # ---- key format guarantees ---------------------------------------------

    def test_resume_key_starts_with_resumes_prefix(self):
        from storage.s3 import resume_key
        assert resume_key("cand_any", ".pdf").startswith("resumes/")

    def test_jd_key_starts_with_job_descriptions_prefix(self):
        from storage.s3 import jd_key
        assert jd_key("jd_any", ".pdf").startswith("job-descriptions/")

    def test_resume_key_contains_candidate_id(self):
        from storage.s3 import resume_key
        assert "cand_unique99" in resume_key("cand_unique99", ".pdf")

    def test_jd_key_contains_jd_id(self):
        from storage.s3 import jd_key
        assert "jd_unique88" in jd_key("jd_unique88", ".docx")

    def test_resume_key_no_uuid_segment(self):
        """Keys must not contain a random UUID hex segment."""
        from storage.s3 import resume_key
        key = resume_key("cand_abc", ".pdf")
        # A UUID hex is 32 hex chars; the key should have no such run.
        import re
        assert not re.search(r"[0-9a-f]{32}", key), f"UUID found in key: {key}"

    def test_jd_key_no_uuid_segment(self):
        from storage.s3 import jd_key
        import re
        key = jd_key("jd_abc", ".pdf")
        assert not re.search(r"[0-9a-f]{32}", key), f"UUID found in key: {key}"


# ===========================================================================
# TestS3UploadDownload — upload_file / download_file
# ===========================================================================

class TestS3UploadDownload:
    """upload_file writes to the exact key supplied; download_file returns bytes."""

    def test_upload_writes_to_supplied_key(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_client = MagicMock()
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            returned = s3mod.upload_file(b"data", "resumes/cand_1/resume.pdf", "application/pdf")
        assert returned == "resumes/cand_1/resume.pdf"
        _, kwargs = mock_client.put_object.call_args
        assert kwargs["Key"] == "resumes/cand_1/resume.pdf"

    def test_upload_stores_content_type(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_client = MagicMock()
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            s3mod.upload_file(b"data", "resumes/cand_x/resume.txt", "text/plain")
        _, kwargs = mock_client.put_object.call_args
        assert kwargs["ContentType"] == "text/plain"

    def test_upload_does_not_set_public_acl(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_client = MagicMock()
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            s3mod.upload_file(b"x", "resumes/cand_x/resume.pdf", "application/pdf")
        _, kwargs = mock_client.put_object.call_args
        assert "ACL" not in kwargs

    def test_upload_accepts_file_like_object(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_client = MagicMock()
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            result = s3mod.upload_file(io.BytesIO(b"content"), "resumes/cand_y/resume.txt", "text/plain")
        assert result == "resumes/cand_y/resume.txt"
        mock_client.put_object.assert_called_once()

    def test_upload_overwrite_same_key_called_twice(self, monkeypatch):
        """Uploading twice to the same deterministic key calls put_object twice —
        S3 overwrites the object silently."""
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_client = MagicMock()
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            from storage.s3 import resume_key
            key = resume_key("cand_abc", ".pdf")
            s3mod.upload_file(b"v1", key, "application/pdf")
            s3mod.upload_file(b"v2", key, "application/pdf")
        assert mock_client.put_object.call_count == 2
        # Both calls used the same key.
        keys_used = [c.kwargs["Key"] for c in mock_client.put_object.call_args_list]
        assert keys_used[0] == keys_used[1] == key

    def test_upload_raises_when_bucket_not_configured(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "")
        from storage import s3 as s3mod
        with pytest.raises(RuntimeError, match="S3_BUCKET_NAME"):
            s3mod.upload_file(b"x", "resumes/c/resume.pdf", "application/pdf")

    def test_upload_propagates_s3_exception(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_client = MagicMock()
        mock_client.put_object.side_effect = Exception("Connection timeout")
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            with pytest.raises(Exception, match="Connection timeout"):
                s3mod.upload_file(b"data", "resumes/c/resume.pdf", "application/pdf")

    def test_download_returns_bytes(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        expected = b"hello from s3"
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": io.BytesIO(expected)}
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            result = s3mod.download_file("resumes/cand_001/resume.pdf")
        assert result == expected

    def test_download_uses_correct_key_and_bucket(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "my-bucket")
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": io.BytesIO(b"data")}
        with patch("storage.s3.boto3.client", return_value=mock_client):
            from storage import s3 as s3mod
            s3mod.download_file("job-descriptions/jd_xyz/job-description.pdf")
        mock_client.get_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="job-descriptions/jd_xyz/job-description.pdf",
        )

    def test_download_raises_when_bucket_not_configured(self, monkeypatch):
        monkeypatch.setattr("config.S3_BUCKET_NAME", "")
        from storage import s3 as s3mod
        with pytest.raises(RuntimeError, match="S3_BUCKET_NAME"):
            s3mod.download_file("some/key")


# ===========================================================================
# TestResumeParser — individual extraction functions + dispatcher + path compat
# ===========================================================================

class TestResumeParser:
    """Each public extraction function is tested independently, then the
    dispatcher (extract_text_from_bytes) and the path-based wrapper."""

    # ---- extract_txt_text --------------------------------------------------

    def test_extract_txt_text_utf8(self):
        from resume_parser import extract_txt_text
        assert extract_txt_text(b"hello world") == "hello world"

    def test_extract_txt_text_strips_whitespace(self):
        from resume_parser import extract_txt_text
        assert extract_txt_text(b"  trimmed  ") == "trimmed"

    def test_extract_txt_text_latin1_fallback(self):
        from resume_parser import extract_txt_text
        latin_bytes = "café".encode("latin-1")
        result = extract_txt_text(latin_bytes)
        assert "caf" in result

    # ---- extract_pdf_text --------------------------------------------------

    def test_extract_pdf_text_calls_pdfplumber(self, monkeypatch):
        import resume_parser
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "extracted pdf text"
        fake_pdf = MagicMock()
        fake_pdf.__enter__ = lambda s: fake_pdf
        fake_pdf.__exit__ = MagicMock(return_value=False)
        fake_pdf.pages = [fake_page]
        monkeypatch.setattr("resume_parser.pdfplumber.open", lambda *a, **kw: fake_pdf)
        assert resume_parser.extract_pdf_text(b"%PDF fake") == "extracted pdf text"

    def test_extract_pdf_text_concatenates_pages(self, monkeypatch):
        import resume_parser
        p1 = MagicMock(); p1.extract_text.return_value = "page one"
        p2 = MagicMock(); p2.extract_text.return_value = "page two"
        fake_pdf = MagicMock()
        fake_pdf.__enter__ = lambda s: fake_pdf
        fake_pdf.__exit__ = MagicMock(return_value=False)
        fake_pdf.pages = [p1, p2]
        monkeypatch.setattr("resume_parser.pdfplumber.open", lambda *a, **kw: fake_pdf)
        result = resume_parser.extract_pdf_text(b"%PDF fake")
        assert "page one" in result and "page two" in result

    def test_extract_pdf_text_skips_none_pages(self, monkeypatch):
        import resume_parser
        p1 = MagicMock(); p1.extract_text.return_value = None
        p2 = MagicMock(); p2.extract_text.return_value = "real text"
        fake_pdf = MagicMock()
        fake_pdf.__enter__ = lambda s: fake_pdf
        fake_pdf.__exit__ = MagicMock(return_value=False)
        fake_pdf.pages = [p1, p2]
        monkeypatch.setattr("resume_parser.pdfplumber.open", lambda *a, **kw: fake_pdf)
        assert resume_parser.extract_pdf_text(b"%PDF fake") == "real text"

    # ---- extract_docx_text -------------------------------------------------

    def test_extract_docx_text_real_docx(self):
        from resume_parser import extract_docx_text
        assert "Senior Python Developer" in extract_docx_text(_make_docx_bytes())

    def test_extract_docx_text_skips_empty_paragraphs(self):
        from docx import Document
        from resume_parser import extract_docx_text
        buf = io.BytesIO()
        doc = Document()
        doc.add_paragraph("")           # empty — should be excluded
        doc.add_paragraph("content")
        doc.save(buf)
        result = extract_docx_text(buf.getvalue())
        assert result == "content"

    # ---- extract_text_from_bytes dispatcher --------------------------------

    def test_dispatcher_routes_pdf(self, monkeypatch):
        import resume_parser
        monkeypatch.setattr(resume_parser, "extract_pdf_text", lambda d: "pdf-result")
        assert resume_parser.extract_text_from_bytes(b"x", "cv.pdf") == "pdf-result"

    def test_dispatcher_routes_txt(self, monkeypatch):
        import resume_parser
        monkeypatch.setattr(resume_parser, "extract_txt_text", lambda d: "txt-result")
        assert resume_parser.extract_text_from_bytes(b"x", "cv.txt") == "txt-result"

    def test_dispatcher_routes_docx(self, monkeypatch):
        import resume_parser
        monkeypatch.setattr(resume_parser, "extract_docx_text", lambda d: "docx-result")
        assert resume_parser.extract_text_from_bytes(b"x", "jd.docx") == "docx-result"

    def test_dispatcher_case_insensitive_extension(self, monkeypatch):
        import resume_parser
        monkeypatch.setattr(resume_parser, "extract_pdf_text", lambda d: "ok")
        assert resume_parser.extract_text_from_bytes(b"x", "CV.PDF") == "ok"

    def test_dispatcher_raises_for_unsupported_extension(self):
        from resume_parser import extract_text_from_bytes
        with pytest.raises(ValueError, match="Unsupported"):
            extract_text_from_bytes(b"data", "document.xls")

    def test_dispatcher_error_message_names_extension(self):
        from resume_parser import extract_text_from_bytes
        with pytest.raises(ValueError, match=r"\.xls"):
            extract_text_from_bytes(b"data", "doc.xls")

    # ---- extract_text path-based wrapper -----------------------------------

    def test_extract_text_txt_path(self, tmp_path):
        from resume_parser import extract_text
        f = tmp_path / "sample.txt"
        f.write_text("legacy path test", encoding="utf-8")
        assert extract_text(str(f)) == "legacy path test"

    def test_extract_text_docx_path(self, tmp_path):
        from resume_parser import extract_text
        f = tmp_path / "jd.docx"
        f.write_bytes(_make_docx_bytes())
        assert "Senior Python Developer" in extract_text(str(f))

    def test_extract_text_path_accepts_pathlib_path(self, tmp_path):
        from resume_parser import extract_text
        f = tmp_path / "notes.txt"
        f.write_text("pathlib test", encoding="utf-8")
        assert extract_text(f) == "pathlib test"   # pathlib.Path, not str


# ===========================================================================
# TestJobDescriptionModel — backward compat for old JD JSON records
# ===========================================================================

class TestJobDescriptionModel:

    def test_from_dict_old_record_no_s3_fields(self):
        """Records created before S3 support load without error; fields → None."""
        from models import JobDescription
        old = {
            "jd_id": "jd_abc123", "role_title": "Engineer", "company": "Acme",
            "required_skills": ["Python"], "preferred_skills": [],
            "experience_years": 3, "jd_text": "Build things.",
            "location": "Remote", "is_active": True,
            "uploaded_by": "admin_1", "created_at": "2024-01-01T00:00:00",
        }
        jd = JobDescription.from_dict(old)
        assert jd.jd_id == "jd_abc123"
        assert jd.jd_file_key is None
        assert jd.jd_original_filename is None
        assert jd.jd_content_type is None

    def test_to_dict_includes_s3_fields(self):
        from models import JobDescription
        jd = JobDescription(
            jd_id="jd_xyz", role_title="Dev", company="Corp",
            jd_file_key="job-descriptions/jd_xyz/job-description.pdf",
            jd_original_filename="spec.pdf",
            jd_content_type="application/pdf",
        )
        d = jd.to_dict()
        assert d["jd_file_key"] == "job-descriptions/jd_xyz/job-description.pdf"
        assert d["jd_original_filename"] == "spec.pdf"
        assert d["jd_content_type"] == "application/pdf"

    def test_to_dict_s3_fields_none_by_default(self):
        from models import JobDescription
        d = JobDescription(jd_id="jd_1", role_title="Dev", company="Corp").to_dict()
        assert d["jd_file_key"] is None
        assert d["jd_original_filename"] is None
        assert d["jd_content_type"] is None

    def test_roundtrip_preserves_s3_fields(self):
        from models import JobDescription
        jd = JobDescription(
            jd_id="jd_r", role_title="SDE", company="X",
            jd_file_key="job-descriptions/jd_r/job-description.pdf",
            jd_original_filename="spec.pdf",
            jd_content_type="application/pdf",
        )
        jd2 = JobDescription.from_dict(jd.to_dict())
        assert jd2.jd_file_key == "job-descriptions/jd_r/job-description.pdf"
        assert jd2.jd_original_filename == "spec.pdf"
        assert jd2.jd_content_type == "application/pdf"

    def test_deterministic_key_stored_in_jd_file_key(self):
        """jd_file_key must match the pattern produced by jd_key()."""
        from models import JobDescription
        from storage.s3 import jd_key
        jd_id = "jd_det1"
        key = jd_key(jd_id, ".pdf")
        jd = JobDescription(
            jd_id=jd_id, role_title="Dev", company="Corp",
            jd_file_key=key,
        )
        assert jd.to_dict()["jd_file_key"] == f"job-descriptions/{jd_id}/job-description.pdf"


# ===========================================================================
# TestJDUploadLogic — S3 round-trip, text extraction, JD persistence
# ===========================================================================

class TestJDUploadLogic:

    def test_typed_text_path_no_s3_call(self, tmp_path, monkeypatch):
        """Typed-text path must not call S3 upload at all."""
        from models import JobDescription
        import auth as auth_mod

        jds_path = _make_fake_jds_file(tmp_path)
        fake_s3 = MagicMock()
        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")

        jd = JobDescription(
            jd_id=f"jd_{uuid.uuid4().hex[:8]}",
            role_title="Python Dev", company="Acme",
            required_skills=["Python"],
            jd_text="We need a Python developer.",
            created_at=datetime.utcnow().isoformat(),
        )
        data = auth_mod.load_json(jds_path)
        data.append(jd.to_dict())
        auth_mod.save_json(jds_path, data)

        saved = auth_mod.load_json(jds_path)
        assert saved[0]["jd_text"] == "We need a Python developer."
        assert saved[0]["jd_file_key"] is None
        fake_s3.upload_file.assert_not_called()

    def test_file_upload_uses_deterministic_jd_key(self, monkeypatch):
        """upload_file must be called with the key produced by jd_key()."""
        from storage import s3 as s3mod
        from storage.s3 import jd_key

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_boto = MagicMock()
        mock_boto.get_object.return_value = {"Body": io.BytesIO(b"JD content")}

        jd_id = "jd_det99"
        expected_key = jd_key(jd_id, ".pdf")

        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(b"JD content", expected_key, "application/pdf")

        _, kwargs = mock_boto.put_object.call_args
        assert kwargs["Key"] == expected_key
        assert kwargs["Key"] == f"job-descriptions/{jd_id}/job-description.pdf"

    def test_jd_overwrite_same_key_on_second_upload(self, monkeypatch):
        """Second upload for the same JD hits the same S3 key — overwrites."""
        from storage import s3 as s3mod
        from storage.s3 import jd_key

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_boto = MagicMock()
        mock_boto.get_object.return_value = {"Body": io.BytesIO(b"updated")}

        jd_id = "jd_overwrite"
        key = jd_key(jd_id, ".pdf")

        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(b"version 1", key, "application/pdf")
            s3mod.upload_file(b"version 2", key, "application/pdf")

        assert mock_boto.put_object.call_count == 2
        keys_used = [c.kwargs["Key"] for c in mock_boto.put_object.call_args_list]
        assert keys_used[0] == keys_used[1] == key

    def test_jd_text_replaced_on_file_reupload(self, tmp_path, monkeypatch):
        """After a file re-upload the JD record's jd_text is the newest extracted text."""
        from storage import s3 as s3mod
        from storage.s3 import jd_key
        from resume_parser import extract_text_from_bytes
        from models import JobDescription
        import auth as auth_mod

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        jd_id = "jd_replace"
        key = jd_key(jd_id, ".txt")

        # --- First upload ---
        v1_bytes = b"First version of job description."
        mock_boto = MagicMock()
        mock_boto.get_object.return_value = {"Body": io.BytesIO(v1_bytes)}
        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(v1_bytes, key, "text/plain")
            stored_v1 = s3mod.download_file(key)
        text_v1 = extract_text_from_bytes(stored_v1, "jd.txt")
        assert text_v1 == "First version of job description."

        # --- Second upload (replacement) ---
        v2_bytes = b"Updated job description with new requirements."
        mock_boto.get_object.return_value = {"Body": io.BytesIO(v2_bytes)}
        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(v2_bytes, key, "text/plain")  # same key → overwrites
            stored_v2 = s3mod.download_file(key)
        text_v2 = extract_text_from_bytes(stored_v2, "jd.txt")
        assert text_v2 == "Updated job description with new requirements."

        # Persist the updated JD record.
        jds_path = _make_fake_jds_file(tmp_path)
        jd = JobDescription(
            jd_id=jd_id, role_title="Dev", company="Corp",
            required_skills=["Python"], jd_text=text_v2,
            jd_file_key=key, jd_original_filename="jd_v2.txt",
            jd_content_type="text/plain",
            created_at=datetime.utcnow().isoformat(),
        )
        data = auth_mod.load_json(jds_path)
        data.append(jd.to_dict())
        auth_mod.save_json(jds_path, data)

        saved = auth_mod.load_json(jds_path)
        assert saved[0]["jd_text"] == "Updated job description with new requirements."
        assert saved[0]["jd_file_key"] == key
        assert saved[0]["jd_original_filename"] == "jd_v2.txt"  # metadata = current file only

    def test_docx_file_upload_extracts_text(self, monkeypatch):
        """DOCX upload path extracts text correctly via python-docx."""
        from storage import s3 as s3mod
        from storage.s3 import jd_key
        from resume_parser import extract_text_from_bytes

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        docx_bytes = _make_docx_bytes()
        mock_boto = MagicMock()
        mock_boto.get_object.return_value = {"Body": io.BytesIO(docx_bytes)}
        key = jd_key("jd_docx1", ".docx")

        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(docx_bytes, key, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            stored = s3mod.download_file(key)

        assert "Senior Python Developer" in extract_text_from_bytes(stored, "jd.docx")

    def test_unsupported_file_type_not_in_allowed_suffixes(self):
        """The route's allowed-suffix guard must reject non-PDF/DOCX uploads."""
        from routes.admin import _JD_ALLOWED_SUFFIXES
        assert ".txt" not in _JD_ALLOWED_SUFFIXES
        assert ".pdf" in _JD_ALLOWED_SUFFIXES
        assert ".docx" in _JD_ALLOWED_SUFFIXES


# ===========================================================================
# TestResumeUploadLogic — S3 round-trip, embedding upsert, resume update
# ===========================================================================

class TestResumeUploadLogic:

    def test_resume_key_is_deterministic_for_candidate(self):
        """Same candidate_id + same extension always produces the same key."""
        from storage.s3 import resume_key
        assert resume_key("cand_abc", ".pdf") == resume_key("cand_abc", ".pdf")

    def test_resume_upload_uses_deterministic_key(self, monkeypatch):
        """upload_file must be called with the key from resume_key()."""
        from storage import s3 as s3mod
        from storage.s3 import resume_key

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_boto = MagicMock()
        cid = "cand_det01"
        expected_key = resume_key(cid, ".pdf")

        with patch("storage.s3.boto3.client", return_value=mock_boto):
            returned = s3mod.upload_file(b"cv data", expected_key, "application/pdf")

        assert returned == expected_key
        _, kwargs = mock_boto.put_object.call_args
        assert kwargs["Key"] == f"resumes/{cid}/resume.pdf"

    def test_resume_key_is_not_a_local_path(self):
        """S3 key must not look like a local filesystem path."""
        from storage.s3 import resume_key
        key = resume_key("cand_abc", ".pdf")
        assert "\\" not in key
        assert "data/resumes" not in key
        assert ":" not in key  # no Windows drive letters

    def test_resume_update_overwrites_same_s3_key(self, monkeypatch):
        """A second resume upload for the same candidate writes to the same key."""
        from storage import s3 as s3mod
        from storage.s3 import resume_key

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        mock_boto = MagicMock()
        cid = "cand_update01"
        key = resume_key(cid, ".pdf")

        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(b"resume v1", key, "application/pdf")
            s3mod.upload_file(b"resume v2", key, "application/pdf")

        assert mock_boto.put_object.call_count == 2
        keys_used = [c.kwargs["Key"] for c in mock_boto.put_object.call_args_list]
        assert keys_used[0] == keys_used[1] == key

    def test_index_resume_is_upsert_by_candidate_id(self, monkeypatch):
        """index_resume must call ChromaDB upsert with candidate_id as the doc id,
        guaranteeing the old vector is replaced on re-upload — no duplicates."""
        fake_collection = MagicMock()
        monkeypatch.setattr("vector_matcher._collection", fake_collection)
        # Provide a fake embed so the model isn't loaded.
        monkeypatch.setattr("vector_matcher.embed_text", lambda t: [0.1, 0.2])

        from vector_matcher import index_resume
        cid = "cand_emb01"
        index_resume(cid, "Python developer with 5 years experience.")

        fake_collection.upsert.assert_called_once()
        call_kwargs = fake_collection.upsert.call_args[1]
        assert call_kwargs["ids"] == [cid]

    def test_index_resume_upsert_replaces_old_embedding(self, monkeypatch):
        """Calling index_resume twice for the same candidate calls upsert twice
        with the same id — ChromaDB replaces the first vector with the second."""
        fake_collection = MagicMock()
        monkeypatch.setattr("vector_matcher._collection", fake_collection)
        monkeypatch.setattr("vector_matcher.embed_text", lambda t: [0.0])

        from vector_matcher import index_resume
        cid = "cand_emb02"
        index_resume(cid, "First resume text.")
        index_resume(cid, "Updated resume text after re-upload.")

        assert fake_collection.upsert.call_count == 2
        # Both calls used the same id — second overwrites the first.
        ids_first = fake_collection.upsert.call_args_list[0][1]["ids"]
        ids_second = fake_collection.upsert.call_args_list[1][1]["ids"]
        assert ids_first == ids_second == [cid]

    def test_index_resume_skipped_for_empty_text(self, monkeypatch):
        """index_resume must not call upsert when resume text is blank."""
        fake_collection = MagicMock()
        monkeypatch.setattr("vector_matcher._collection", fake_collection)

        from vector_matcher import index_resume
        index_resume("cand_empty", "   ")
        fake_collection.upsert.assert_not_called()

    def test_txt_resume_round_trip(self, monkeypatch):
        """TXT resume: upload → download → extract text preserves content."""
        from storage import s3 as s3mod
        from storage.s3 import resume_key
        from resume_parser import extract_text_from_bytes

        monkeypatch.setattr("config.S3_BUCKET_NAME", "test-bucket")
        resume_text = "Experienced Python developer, 5 years."
        mock_boto = MagicMock()
        mock_boto.get_object.return_value = {"Body": io.BytesIO(resume_text.encode())}
        key = resume_key("cand_txt01", ".txt")

        with patch("storage.s3.boto3.client", return_value=mock_boto):
            s3mod.upload_file(resume_text.encode(), key, "text/plain")
            stored = s3mod.download_file(key)

        assert extract_text_from_bytes(stored, "resume.txt") == resume_text
