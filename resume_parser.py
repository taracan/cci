"""Resume text extraction only.

NO hardcoded skill lists. NO regex rules. NO keyword matching.
All structured extraction (skills, experience, education) is done by the LLM
in llm/llm_service.py. This module ONLY turns a PDF/TXT file into raw text.
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber


def extract_text(file_path: str) -> str:
    """Extract raw text from a PDF or TXT resume file.

    Args:
        file_path: Path to the resume file (.pdf or .txt).

    Returns:
        The extracted plain text.
    """
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        text = ""
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        return text.strip()
    return path.read_text(encoding="utf-8").strip()
