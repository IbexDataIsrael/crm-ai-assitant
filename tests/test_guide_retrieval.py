"""Retrieval over the active knowledge: the reviewed English and Hebrew user guides.

Uses the real local model and the app's real store (knowledge/). No generation.
Expected passages were chosen before the first run, from the guide text.
"""

import pytest

from app import knowledge

GUIDE_QUESTIONS = [
    (
        "Why can't I see a contact that the assistant added?",
        "about_english.md",
        "Pending approval",
    ),
    (
        "שכחתי את הסיסמה שלי, מה עושים?",
        "about_hebrew.md",
        "שכחתם סיסמה",
    ),
]


@pytest.mark.parametrize("question, filename, expected_text", GUIDE_QUESTIONS)
def test_guide_question_finds_expected_passage_first(question, filename, expected_text):
    results = knowledge.search(question)
    ranking = [(r["filename"], r["score"], r["text"][:60]) for r in results]
    print(question, ranking)
    assert len(results) == 3
    assert results[0]["filename"] == filename, ranking
    assert expected_text in results[0]["text"], ranking


# "What is it?" in both languages must retrieve the system's explanation, not
# only the guide title and the "1. What this system is" heading (instructions7).
WHAT_IS_QUESTIONS = [
    ("what is jewishpoints", "about_english.md", "A bilingual (Hebrew/English) CRM"),
    ("מה זה נקודות יהודיות", "about_hebrew.md", "מערכת CRM דו-לשונית"),
]


@pytest.mark.parametrize("question, filename, explanation", WHAT_IS_QUESTIONS)
def test_what_is_question_retrieves_the_explanation_first(question, filename, explanation):
    results = knowledge.search(question)
    ranking = [(r["filename"], r["score"], r["heading"], r["text"][:60]) for r in results]
    print(question, ranking)
    assert results[0]["filename"] == filename, ranking
    assert results[0]["text"].startswith(explanation), ranking
