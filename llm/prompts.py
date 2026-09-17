"""Prompt builders for resume parsing, JD parsing, and match explanation.

Every prompt asks for STRICT JSON so the service can parse it deterministically.
No hardcoded skill/education lists — the LLM extracts everything from the text.
"""
from __future__ import annotations


def build_resume_parse_prompt(resume_text: str) -> str:
    """Prompt that extracts structured fields from raw resume text.

    Args:
        resume_text: The raw resume text.

    Returns:
        A prompt string instructing the model to return strict JSON.
    """
    return f"""You are an expert technical recruiter. Read the RESUME below and
extract structured data. Infer years of experience from job date ranges if not
stated explicitly. Return ONLY valid JSON, no prose, in exactly this shape:
{{
  "skills": ["<skill>", ...],
  "experience_years": <number>,
  "education": "<highest level, e.g. B.Tech / Masters / PhD>",
  "summary": "<2-sentence profile summary>"
}}

RESUME:
{resume_text}
"""


def build_jd_parse_prompt(jd_text: str) -> str:
    """Prompt that extracts structured requirements from raw JD text.

    Args:
        jd_text: The raw job description text.

    Returns:
        A prompt string instructing the model to return strict JSON.
    """
    return f"""You are an expert technical recruiter. Read the JOB DESCRIPTION
below and extract requirements. Return ONLY valid JSON, no prose, in this shape:
{{
  "required_skills": ["<skill>", ...],
  "preferred_skills": ["<skill>", ...],
  "experience_years": <number>,
  "role_title": "<title>"
}}

JOB DESCRIPTION:
{jd_text}
"""


def build_match_explain_prompt(resume_text: str, jd_text: str) -> str:
    """Prompt that scores + explains a resume↔JD match.

    Args:
        resume_text: The candidate's raw resume text.
        jd_text: The job description text.

    Returns:
        A prompt string instructing the model to return strict JSON.
    """
    return f"""You are a senior hiring manager. Compare the RESUME to the JOB
DESCRIPTION. Judge fit on skills, experience, and relevance. Return ONLY valid
JSON, no prose, in this shape:
{{
  "overall_score": <0-100>,
  "matched_skills": ["<skill>", ...],
  "missing_skills": ["<skill>", ...],
  "verdict": "<Strong Match | Partial Match | Weak Match>",
  "reasoning": "<3-4 sentence explanation of the fit>"
}}

JOB DESCRIPTION:
{jd_text}

RESUME:
{resume_text}
"""
