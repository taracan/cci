"""Unit tests for matching_engine — pure scoring logic, no external deps."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from matching_engine import (
    calculate_skill_score,
    calculate_experience_score,
    calculate_role_relevance,
    calculate_match,
)


class TestSkillScore:
    def test_full_match(self):
        score, matched, missing = calculate_skill_score(
            ["Python", "AWS", "Docker"], ["Python", "AWS", "Docker"], [])
        assert score == 100.0
        assert len(matched) == 3 and len(missing) == 0

    def test_partial_match(self):
        score, matched, missing = calculate_skill_score(
            ["Python", "Flask"], ["Python", "AWS", "Docker", "Flask"], [])
        assert score == 50.0
        assert len(matched) == 2 and len(missing) == 2

    def test_no_match(self):
        score, matched, missing = calculate_skill_score(
            ["Java"], ["Python", "AWS"], [])
        assert score == 0.0

    def test_preferred_bonus(self):
        score, _, _ = calculate_skill_score(
            ["Python", "Kubernetes"], ["Python", "AWS"], ["Kubernetes"])
        assert score == 55.0   # 50% + 5 bonus

    def test_empty_required_returns_100(self):
        score, matched, missing = calculate_skill_score(["Python"], [], [])
        assert score == 100.0

    def test_case_insensitive(self):
        score, _, _ = calculate_skill_score(["python"], ["Python"], [])
        assert score == 100.0


class TestExperienceScore:
    def test_meets_requirement(self):
        assert calculate_experience_score(5, 3) == 100.0

    def test_exact_requirement(self):
        assert calculate_experience_score(3, 3) == 100.0

    def test_below_requirement(self):
        assert calculate_experience_score(2, 4) == 50.0

    def test_zero_required_always_100(self):
        assert calculate_experience_score(0, 0) == 100.0

    def test_zero_candidate(self):
        assert calculate_experience_score(0, 5) == 0.0


class TestRoleRelevance:
    def test_exact_match(self):
        assert calculate_role_relevance("Python Developer", "Python Developer") == 100.0

    def test_no_overlap_baseline(self):
        assert calculate_role_relevance("Chef", "Python Developer") == 20.0

    def test_partial_overlap(self):
        score = calculate_role_relevance("Python Developer", "Senior Python Engineer")
        assert 50 <= score <= 100

    def test_empty_inputs(self):
        assert calculate_role_relevance("", "Python Developer") == 20.0
        assert calculate_role_relevance("Python Developer", "") == 20.0


class TestCalculateMatch:
    def test_keyword_only_structure(self):
        r = calculate_match(["Python", "AWS"], 4, "Python Developer",
                            ["Python", "AWS", "Kubernetes"], [], 3, "Python Developer")
        for key in ("overall_score", "skill_score", "experience_score",
                    "role_score", "semantic_score", "matched_skills", "missing_skills"):
            assert key in r
        assert r["semantic_score"] is None
        assert "Kubernetes" in r["missing_skills"]

    def test_keyword_only_weighting(self):
        # skills=50, exp=100, role=100 -> 50*.6 + 100*.25 + 100*.15 = 70
        r = calculate_match(["Python"], 5, "Python Developer",
                            ["Python", "AWS"], [], 3, "Python Developer")
        assert r["skill_score"] == 50.0
        assert r["overall_score"] == pytest.approx(70.0, abs=0.5)

    def test_with_semantic_weighting(self):
        # skills=50, semantic=80, exp=100, role=100
        # 50*.40 + 80*.35 + 100*.15 + 100*.10 = 20+28+15+10 = 73
        r = calculate_match(["Python"], 5, "Python Developer",
                            ["Python", "AWS"], [], 3, "Python Developer",
                            semantic_score=80.0)
        assert r["semantic_score"] == 80.0
        assert r["overall_score"] == pytest.approx(73.0, abs=0.5)

    def test_zero_skills_valid(self):
        r = calculate_match([], 0, "", ["Python"], [], 2, "Python Developer")
        assert r["skill_score"] == 0.0
        assert r["overall_score"] >= 0
