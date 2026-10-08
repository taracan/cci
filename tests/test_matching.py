"""Tests for the matching engine."""
import sys
from pathlib import Path

# Add parent to path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from matching_engine import (
    calculate_skill_score,
    calculate_experience_score,
    calculate_role_relevance,
    calculate_match,
)


def test_skill_score_full_match():
    """All required skills matched should give 100 (with preferred bonus capped)."""
    score, matched, missing = calculate_skill_score(
        candidate_skills=["Python", "AWS", "Docker"],
        required_skills=["Python", "AWS", "Docker"],
        preferred_skills=["Kubernetes"],
    )
    assert score == 100.0
    assert len(matched) == 3
    assert len(missing) == 0


def test_skill_score_partial_match():
    """Partial match should give proportional score."""
    score, matched, missing = calculate_skill_score(
        candidate_skills=["Python", "Flask"],
        required_skills=["Python", "AWS", "Docker", "Flask"],
        preferred_skills=[],
    )
    assert score == 50.0
    assert len(matched) == 2
    assert len(missing) == 2


def test_skill_score_no_match():
    """No skills matched should give 0."""
    score, matched, missing = calculate_skill_score(
        candidate_skills=["Java", "Spring"],
        required_skills=["Python", "AWS"],
        preferred_skills=[],
    )
    assert score == 0.0
    assert len(matched) == 0
    assert len(missing) == 2


def test_skill_score_preferred_bonus():
    """Having preferred skills should add 5 points."""
    score, _, _ = calculate_skill_score(
        candidate_skills=["Python", "Kubernetes"],
        required_skills=["Python", "AWS"],
        preferred_skills=["Kubernetes"],
    )
    # 50% base (1/2 required) + 5 bonus = 55
    assert score == 55.0


def test_experience_score_meets_requirement():
    """Meeting or exceeding requirement gives 100."""
    assert calculate_experience_score(5, 3) == 100.0
    assert calculate_experience_score(3, 3) == 100.0


def test_experience_score_below_requirement():
    """Below requirement gives proportional score."""
    assert calculate_experience_score(2, 4) == 50.0
    assert calculate_experience_score(1, 5) == 20.0


def test_experience_score_zero_required():
    """Zero requirement means always 100."""
    assert calculate_experience_score(0, 0) == 100.0


def test_role_relevance_exact_match():
    """Exact role match gives 100."""
    assert calculate_role_relevance("Python Developer", "Python Developer") == 100.0


def test_role_relevance_no_match():
    """No overlap gives baseline 20."""
    assert calculate_role_relevance("Chef", "Python Developer") == 20.0


def test_role_relevance_partial_match():
    """Partial overlap gives score between 50-100."""
    score = calculate_role_relevance("Python Developer", "Senior Python Engineer")
    assert 50 <= score <= 100


def test_calculate_match_overall():
    """Full match calculation produces expected structure."""
    result = calculate_match(
        candidate_skills=["Python", "AWS", "Docker"],
        candidate_exp=4,
        candidate_role="Python Developer",
        jd_required=["Python", "AWS", "Docker", "Kubernetes"],
        jd_preferred=["Terraform"],
        jd_exp=3,
        jd_title="Python Developer",
    )
    assert "overall_score" in result
    assert "skill_score" in result
    assert "experience_score" in result
    assert "role_score" in result
    assert "matched_skills" in result
    assert "missing_skills" in result
    assert result["experience_score"] == 100.0
    assert result["role_score"] == 100.0
    assert "Kubernetes" in result["missing_skills"]
    assert result["overall_score"] > 0


def test_calculate_match_zero_skills():
    """No skills at all should still return valid result."""
    result = calculate_match(
        candidate_skills=[],
        candidate_exp=0,
        candidate_role="",
        jd_required=["Python"],
        jd_preferred=[],
        jd_exp=2,
        jd_title="Python Developer",
    )
    assert result["skill_score"] == 0.0
    assert result["overall_score"] >= 0


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"  PASS: {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL: {test.__name__} — {e}")

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
