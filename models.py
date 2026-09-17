"""Data models for the Resume-JD Matching Portal."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Candidate:
    candidate_id: str
    name: str
    email: str
    password_hash: str
    target_role: str = ""
    resume_uploaded: bool = False
    resume_file: str = ""
    role: str = "candidate"
    registered_at: str = ""

    def to_dict(self):
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "email": self.email,
            "password_hash": self.password_hash,
            "target_role": self.target_role,
            "resume_uploaded": self.resume_uploaded,
            "resume_file": self.resume_file,
            "role": self.role,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Admin:
    admin_id: str
    name: str
    email: str
    password_hash: str
    role: str = "admin"
    registered_at: str = ""

    def to_dict(self):
        return {
            "admin_id": self.admin_id,
            "name": self.name,
            "email": self.email,
            "password_hash": self.password_hash,
            "role": self.role,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class JobDescription:
    jd_id: str
    role_title: str
    company: str
    required_skills: list = field(default_factory=list)
    preferred_skills: list = field(default_factory=list)
    experience_years: int = 0
    jd_text: str = ""
    location: str = "Remote"
    is_active: bool = True
    uploaded_by: str = ""
    created_at: str = ""

    def to_dict(self):
        return {
            "jd_id": self.jd_id,
            "role_title": self.role_title,
            "company": self.company,
            "required_skills": self.required_skills,
            "preferred_skills": self.preferred_skills,
            "experience_years": self.experience_years,
            "jd_text": self.jd_text,
            "location": self.location,
            "is_active": self.is_active,
            "uploaded_by": self.uploaded_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ParsedResume:
    raw_text: str = ""
    skills: list = field(default_factory=list)
    experience_years: float = 0.0
    education: str = ""

    def to_dict(self):
        return {
            "raw_text": self.raw_text,
            "skills": self.skills,
            "experience_years": self.experience_years,
            "education": self.education,
        }


@dataclass
class MatchResult:
    overall_score: float = 0.0
    skill_score: float = 0.0
    experience_score: float = 0.0
    role_score: float = 0.0
    matched_skills: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)
