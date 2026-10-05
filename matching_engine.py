"""Matching engine — scores candidates against JDs."""
from role_titles import normalize_role, split_target_roles


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
    """Calculate role relevance using keyword overlap.

    ``candidate_role`` may hold several roles ("Data Analyst, ML Engineer");
    they are split (see role_titles.split_target_roles), each is scored
    against the JD title, and the BEST score is returned. Passing one role
    (e.g. the one picked on Find Jobs) scores just that role.

    Titles are normalised first (see role_titles.py): punctuation is handled
    and synonyms/abbreviations are mapped to one form, so "Sr. ML Engineer"
    and "Senior Machine Learning Engineer" count as the same words.
    """
    return best_role_match(candidate_role, jd_role_title)[0]


def best_role_match(candidate_role, jd_role_title):
    """Score every target role against the JD title and report the winner(s).

    Returns:
        (best_score, best_roles): the highest role score, and every role that
        reached it, in the order the candidate typed them (several when tied).
        With no roles or no JD title: (20.0, []).
    """
    roles = split_target_roles(candidate_role)
    if not roles or not jd_role_title:
        return 20.0, []
    scored = [(role, _single_role_relevance(role, jd_role_title)) for role in roles]
    best = max(score for _, score in scored)
    return best, [role for role, score in scored if score == best]


def _single_role_relevance(candidate_role, jd_role_title):
    """Role score for ONE candidate role vs a JD title (20 floor, 100 = same words)."""
    cand_words = normalize_role(candidate_role)
    jd_words = normalize_role(jd_role_title)

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
    # With several target roles, the best one counts; best_roles says which.
    role_score, best_roles = best_role_match(candidate_role, jd_title)

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
        "best_roles": best_roles,
        "semantic_score": semantic_score,
        "matched_skills": matched,
        "missing_skills": missing,
    }
