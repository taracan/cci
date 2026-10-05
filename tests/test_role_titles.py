"""Unit tests for role_titles.py and its use in the role score."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from role_titles import tokenize, apply_synonyms, normalize_role, split_target_roles, SYNONYMS
from matching_engine import calculate_role_relevance


class TestPunctuation:
    @pytest.mark.parametrize("title, words", [
        ("AI/ML Engineer", ["ai", "ml", "engineer"]),
        ("Full-Stack Developer", ["full", "stack", "developer"]),
        ("Sr. Engineer", ["sr", "engineer"]),
        ("A.I. Engineer", ["ai", "engineer"]),
        ("Engineer (Remote), Backend", ["engineer", "remote", "backend"]),
        ("Data & Analytics | Lead", ["data", "analytics", "lead"]),
    ])
    def test_symbols_split_or_dropped(self, title, words):
        assert tokenize(title) == words

    @pytest.mark.parametrize("title, token", [
        ("Node.js Developer", "nodejs"),
        ("Node JS Developer", "nodejs"),
        ("Vue.js Developer", "vuejs"),
        ("C++ Engineer", "cpp"),
        ("C# Developer", "csharp"),
        (".NET Developer", "dotnet"),
    ])
    def test_tech_names_with_symbols_are_protected(self, title, token):
        assert token in tokenize(title)

    def test_empty_and_none(self):
        assert tokenize("") == []
        assert tokenize(None) == []
        assert tokenize("!!! ---") == []


class TestSynonyms:
    def test_longest_phrase_wins(self):
        assert apply_synonyms(["site", "reliability", "engineer"]) == ["sre"]

    def test_expansion_to_several_words(self):
        assert apply_synonyms(["swe"]) == ["software", "engineer"]

    def test_unknown_words_untouched(self):
        assert apply_synonyms(["python", "developer"]) == ["python", "developer"]

    def test_canonical_output_not_remapped(self):
        # "dev" -> "developer" must not be treated as input again.
        assert apply_synonyms(["dev", "ops"]) == ["devops"]
        assert apply_synonyms(["dev"]) == ["developer"]

    def test_every_synonym_key_is_already_tokenized(self):
        # Keys must look like tokenize() output, or they could never match.
        for key in SYNONYMS:
            assert tokenize(key) == key.split(), key

    @pytest.mark.parametrize("a, b", [
        ("ML Engineer", "Machine Learning Engineer"),
        ("Machine-Learning Engineer", "ML engineer"),
        ("Sr. Python Dev", "Senior Python Developer"),
        ("Full-Stack Developer", "Fullstack Developer"),
        ("Front End Engineer", "Frontend Engineer"),
        ("SRE", "Site Reliability Engineer"),
        ("SWE", "Software Engineer"),
        ("Node.js Developer", "NodeJS Developer"),
        ("AI Engineer", "Artificial Intelligence Engineer"),
        ("NLP Engineer", "Natural Language Processing Engineer"),
    ])
    def test_equivalent_titles_normalize_equal(self, a, b):
        assert normalize_role(a) == normalize_role(b)

    def test_filler_removed_after_synonyms(self):
        assert normalize_role("Head of Machine Learning") == {"head", "ml"}


class TestRoleRelevance:
    def test_synonym_titles_score_100(self):
        assert calculate_role_relevance("ML Engineer", "Machine Learning Engineer") == 100.0

    def test_abbreviation_and_punctuation_score_100(self):
        assert calculate_role_relevance("Sr. Full-Stack Dev", "Senior Fullstack Developer") == 100.0

    def test_partial_overlap_uses_normalized_words(self):
        # {ml, engineer} vs {ai, llm, engineer}: 1 shared of 4 → 50 + 0.25*50
        assert calculate_role_relevance("ML Engineer", "AI/LLM Engineer") == 62.5

    def test_floor_unchanged(self):
        assert calculate_role_relevance("Chef", "ML Engineer") == 20.0
        assert calculate_role_relevance("", "ML Engineer") == 20.0
        assert calculate_role_relevance("!!!", "ML Engineer") == 20.0


class TestSplitTargetRoles:
    @pytest.mark.parametrize("text, roles", [
        ("Data Analyst, ML Engineer", ["Data Analyst", "ML Engineer"]),
        ("Data Analyst; ML Engineer", ["Data Analyst", "ML Engineer"]),
        ("Data Analyst | ML Engineer", ["Data Analyst", "ML Engineer"]),
        ("Data Analyst or ML Engineer", ["Data Analyst", "ML Engineer"]),
        ("Data Analyst OR ML Engineer", ["Data Analyst", "ML Engineer"]),
        ("Data Analyst\nML Engineer", ["Data Analyst", "ML Engineer"]),
        ("Data Analyst, or ML Engineer", ["Data Analyst", "ML Engineer"]),
        ("A, B; C | D or E", ["A", "B", "C", "D", "E"]),
    ])
    def test_separators(self, text, roles):
        assert split_target_roles(text) == roles

    def test_slash_does_not_split(self):
        assert split_target_roles("AI/ML Engineer, Data Scientist") == ["AI/ML Engineer", "Data Scientist"]

    def test_or_inside_a_word_does_not_split(self):
        assert split_target_roles("Editor, Operations Director") == ["Editor", "Operations Director"]

    def test_single_role_unchanged(self):
        assert split_target_roles("  Python   Developer ") == ["Python Developer"]

    def test_blank_parts_and_duplicates_dropped(self):
        assert split_target_roles("Data Analyst,, data  analyst , ,ML Engineer,") == ["Data Analyst", "ML Engineer"]

    def test_empty(self):
        assert split_target_roles("") == []
        assert split_target_roles(None) == []
        assert split_target_roles(" , ; ") == []


class TestMultiRoleRelevance:
    def test_best_role_wins(self):
        both = "Data Analyst, ML Engineer"
        assert calculate_role_relevance(both, "Machine Learning Engineer") == 100.0
        assert calculate_role_relevance(both, "Data Analyst") == 100.0

    def test_words_are_not_mixed_across_roles(self):
        # "Data Engineer" borrows one word from each role; neither role fully matches.
        # Best single role: {data, analyst} vs {data, engineer} → 50 + (1/3)*50
        assert calculate_role_relevance("Data Analyst, ML Engineer", "Data Engineer") == 66.7

    def test_no_role_matches_gives_floor(self):
        assert calculate_role_relevance("Data Analyst, ML Engineer", "Chef") == 20.0

    def test_one_role_scores_only_that_role(self):
        assert calculate_role_relevance("Data Analyst", "ML Engineer") == 20.0


class TestBestRoleMatch:
    def test_reports_winning_role(self):
        from matching_engine import best_role_match
        assert best_role_match("Data Analyst, ML Engineer", "Machine Learning Engineer") == (100.0, ["ML Engineer"])

    def test_ties_list_every_winner_in_typed_order(self):
        from matching_engine import best_role_match
        assert best_role_match("Data Analyst, ML Engineer", "Data Engineer") == (66.7, ["Data Analyst", "ML Engineer"])

    def test_no_roles(self):
        from matching_engine import best_role_match
        assert best_role_match("", "ML Engineer") == (20.0, [])

    def test_calculate_match_exposes_best_roles(self):
        from matching_engine import calculate_match
        r = calculate_match(["Python"], 3, "Data Analyst, ML Engineer", ["Python"], [], 2, "ML Engineer")
        assert r["role_score"] == 100.0 and r["best_roles"] == ["ML Engineer"]
