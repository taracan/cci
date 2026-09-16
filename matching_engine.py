"""Matching engine — scores candidates against JDs."""


def calculate_skill_score(candidate_skills, required_skills, preferred_skills):
    """Calculate skill match percentage."""
    if not required_skills:
        return 100.0, [], []

    cand_lower = {s.lower() for s in candidate_skills}
    req_lower = {s.lower() for s in required_skills}
    pref_lower = {s.lower() for s in preferred_skills}

    matched = cand_lower & req_lower
    missing = req_lower - cand_lower

    score = (len(matched) / len(req_lower)) * 100
    if cand_lower & pref_lower:
        score = min(score + 5, 100.0)

    matched_display = [s for s in required_skills if s.lower() in matched]
    missing_display = [s for s in required_skills if s.lower() in missing]
    return round(score, 1), matched_display, missing_display


def calculate_experience_score(candidate_years, required_years):
    """Calculate experience match."""
    if required_years <= 0:
        return 100.0
    if candidate_years >= required_years:
        return 100.0
    return round((candidate_years / required_years) * 100, 1)


def calculate_role_relevance(candidate_role, jd_role_title):
    """Calculate role relevance using keyword overlap."""
    if not candidate_role or not jd_role_title:
        return 20.0

    filler = {"a", "an", "the", "of", "in", "for", "and", "or", "at", "to", "with"}
    cand_words = set(candidate_role.lower().split()) - filler
    jd_words = set(jd_role_title.lower().split()) - filler

    if not cand_words or not jd_words:
        return 20.0
    if cand_words == jd_words:
        return 100.0

    overlap = cand_words & jd_words
    if not overlap:
        return 20.0

    ratio = len(overlap) / len(cand_words | jd_words)
    return round(50 + (ratio * 50), 1)


def calculate_match(candidate_skills, candidate_exp, candidate_role,
                    jd_required, jd_preferred, jd_exp, jd_title,
                    semantic_score=None):
    """Full match calculation. Returns dict with score + breakdown.

    When ``semantic_score`` (0–100 from the vector matcher) is provided, the
    weighting shifts to blend keyword accuracy with semantic understanding:
        skills 40% + semantic 35% + experience 15% + role 10%
    Without it, the original keyword-only weighting is used:
        skills 60% + experience 25% + role 15%
    """
    skill_score, matched, missing = calculate_skill_score(
        candidate_skills, jd_required, jd_preferred
    )
    exp_score = calculate_experience_score(candidate_exp, jd_exp)
    role_score = calculate_role_relevance(candidate_role, jd_title)

    if semantic_score is not None:
        overall = round(
            skill_score * 0.40
            + semantic_score * 0.35
            + exp_score * 0.15
            + role_score * 0.10,
            1,
        )
    else:
        overall = round(skill_score * 0.6 + exp_score * 0.25 + role_score * 0.15, 1)

    return {
        "overall_score": overall,
        "skill_score": skill_score,
        "experience_score": exp_score,
        "role_score": role_score,
        "semantic_score": semantic_score,
        "matched_skills": matched,
        "missing_skills": missing,
    }
