"""Document text extraction for resumes and job descriptions.

NO hardcoded skill lists. NO regex rules. NO keyword matching.
All structured extraction (skills, experience, education) is delegated to
the LLM in llm/llm_service.py.  This module ONLY turns a file into raw text.

Supported formats
-----------------
- PDF  (.pdf)  — via pdfplumber
- TXT  (.txt)  — plain UTF-8 / latin-1 read
- DOCX (.docx) — via python-docx (used for JD document uploads)

Entry points
------------
extract_pdf_text(data)   — extract text from PDF bytes
extract_txt_text(data)   — decode plain-text bytes
extract_docx_text(data)  — extract text from DOCX bytes

extract_text_from_bytes(data, filename)  — dispatch by file extension (bytes)
extract_text(file_path)                  — backward-compatible path-based entry
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Union

import pdfplumber
from docx import Document as DocxDocument


# ---------------------------------------------------------------------------
# Public extraction functions (one per format)
# ---------------------------------------------------------------------------

def extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF *data* bytes using pdfplumber.

    Parameters
    ----------
    data:
        Raw PDF file content.

    Returns
    -------
    str
        Concatenated plain text from all pages, stripped of leading/trailing
        whitespace.
    """
    text = ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text.strip()


def extract_txt_text(data: bytes) -> str:
    """Decode plain-text *data* bytes as UTF-8, falling back to latin-1.

    Parameters
    ----------
    data:
        Raw text file content.

    Returns
    -------
    str
        Decoded and stripped string.
    """
    try:
        return data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return data.decode("latin-1").strip()


def extract_docx_text(data: bytes) -> str:
    """Extract paragraph text from DOCX *data* bytes using python-docx.

    Parameters
    ----------
    data:
        Raw DOCX file content.

    Returns
    -------
    str
        Non-empty paragraphs joined by newlines, stripped.
    """
    doc = DocxDocument(io.BytesIO(data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip()


# ---------------------------------------------------------------------------
# Dispatcher — selects the right function from the file extension
# ---------------------------------------------------------------------------

def extract_text_from_bytes(data: bytes, filename: str) -> str:
    """Extract raw text from *data* bytes, dispatching on *filename*'s extension.

    Parameters
    ----------
    data:
        Raw file content (e.g. downloaded from S3).
    filename:
        Original filename — used **only** to determine file type; the file
        does not need to exist on disk.

    Returns
    -------
    str
        Extracted plain text.

    Raises
    ------
    ValueError
        If the file extension is not ``.pdf``, ``.txt``, or ``.docx``.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(data)
    if suffix == ".docx":
        return extract_docx_text(data)
    if suffix == ".txt":
        return extract_txt_text(data)
    raise ValueError(
        f"Unsupported file type '{suffix}'. Accepted: .pdf, .txt, .docx"
    )


# ---------------------------------------------------------------------------
# Backward-compatible path-based entry point
# ---------------------------------------------------------------------------

def extract_text(file_path: Union[str, Path]) -> str:
    """Extract raw text from a local PDF, TXT, or DOCX file.

    Preserved for any callers that pass a local path rather than bytes.
    Internally reads the file and delegates to :func:`extract_text_from_bytes`.

    Parameters
    ----------
    file_path:
        Path to the file on disk.

    Returns
    -------
    str
        Extracted plain text.
    """
    path = Path(file_path)
    return extract_text_from_bytes(path.read_bytes(), path.name)
