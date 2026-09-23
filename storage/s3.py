"""Reusable AWS S3 storage helpers for the TalentLink platform.

All credentials are sourced from the environment (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION) — never hardcoded.

Deterministic key scheme
------------------------
One current document per candidate / per JD.  Uploading a new file to the
same entity always overwrites the previous object at the same key, so there
is never more than one live document per candidate or JD.

  resumes/<candidate_id>/resume<ext>          e.g. resumes/cand_abc/resume.pdf
  job-descriptions/<jd_id>/job-description<ext>   e.g. job-descriptions/jd_1/job-description.docx

Public API
----------
resume_key(candidate_id, extension) -> str
    Return the deterministic S3 key for a candidate's current resume.

jd_key(jd_id, extension) -> str
    Return the deterministic S3 key for a JD's current uploaded document.

upload_file(file_obj, object_key, content_type) -> str
    Upload bytes / file-like object to *object_key*; return that key.

download_file(object_key) -> bytes
    Retrieve an object by key; return raw bytes.
"""
from __future__ import annotations

import io
from typing import Union

import boto3

import config


# ---------------------------------------------------------------------------
# Deterministic key helpers
# ---------------------------------------------------------------------------

def resume_key(candidate_id: str, extension: str) -> str:
    """Return the deterministic S3 key for a candidate's current resume.

    Parameters
    ----------
    candidate_id:
        The candidate's unique identifier (e.g. ``"cand_abc123"``).
    extension:
        File extension **including the leading dot** (e.g. ``".pdf"``).

    Returns
    -------
    str
        S3 object key, e.g. ``"resumes/cand_abc123/resume.pdf"``.
    """
    ext = extension.lower().lstrip(".")
    return f"resumes/{candidate_id}/resume.{ext}"


def jd_key(jd_id: str, extension: str) -> str:
    """Return the deterministic S3 key for a JD's current uploaded document.

    Parameters
    ----------
    jd_id:
        The JD's unique identifier (e.g. ``"jd_def456"``).
    extension:
        File extension **including the leading dot** (e.g. ``".docx"``).

    Returns
    -------
    str
        S3 object key, e.g. ``"job-descriptions/jd_def456/job-description.docx"``.
    """
    ext = extension.lower().lstrip(".")
    return f"job-descriptions/{jd_id}/job-description.{ext}"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_client():
    """Return a boto3 S3 client using environment credentials and region."""
    return boto3.client("s3", region_name=config.AWS_DEFAULT_REGION)


def _check_bucket():
    if config.S3_BUCKET_NAME == "":
        raise RuntimeError(
            "S3_BUCKET_NAME is not configured. "
            "Set it in your environment or .env file."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_file(
    file_obj: Union[bytes, io.IOBase],
    object_key: str,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload *file_obj* to the configured private S3 bucket at *object_key*.

    Because the key is deterministic (built by :func:`resume_key` or
    :func:`jd_key`), uploading a new file for the same entity silently
    overwrites the previous object — there is never more than one live
    document per candidate or JD.

    Parameters
    ----------
    file_obj:
        Raw bytes or a readable file-like object.
    object_key:
        The full S3 object key (e.g. as returned by :func:`resume_key`).
    content_type:
        MIME type stored in S3 metadata.

    Returns
    -------
    str
        The *object_key* that was written (same value passed in).
    """
    _check_bucket()

    # Normalise to a seekable BytesIO so boto3 always gets a stream.
    if isinstance(file_obj, (bytes, bytearray)):
        body = io.BytesIO(file_obj)
    else:
        body = file_obj
        if hasattr(body, "seek"):
            body.seek(0)

    client = _get_client()
    client.put_object(
        Bucket=config.S3_BUCKET_NAME,
        Key=object_key,
        Body=body,
        ContentType=content_type,
        # No ACL — bucket stays private (default for modern buckets)
    )
    return object_key


def download_file(object_key: str) -> bytes:
    """Retrieve an S3 object by *object_key* and return its content as bytes.

    Parameters
    ----------
    object_key:
        The full S3 key as returned by :func:`resume_key`, :func:`jd_key`,
        or :func:`upload_file`.

    Returns
    -------
    bytes
        Raw file content.

    Raises
    ------
    botocore.exceptions.ClientError
        Re-raised as-is when the object is not found or access is denied.
    RuntimeError
        When S3_BUCKET_NAME is not configured.
    """
    _check_bucket()
    client = _get_client()
    response = client.get_object(Bucket=config.S3_BUCKET_NAME, Key=object_key)
    return response["Body"].read()
