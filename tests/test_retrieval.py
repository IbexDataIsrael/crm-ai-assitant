"""Retrieval tests with the real model.

Each question is a paraphrase that avoids the main words of the source text,
so a pass means the match came from meaning, not shared keywords.
"""

import pytest

from app import knowledge

PARAPHRASES = [
    (
        "Someone just gave me banknotes as a gift for the charity, what do I do with them?",
        "donations.md",
        "Accepting a cash donation",
    ),
    (
        "I'm ill and won't make tomorrow's appointment at someone's place - who should I let know?",
        "visits.md",
        "Cancelling a visit",
    ),
    (
        "What should I jot down once I hang up with someone?",
        "sample.md",
        "SAMPLE DATA - fictional volunteer instructions",
    ),
]


@pytest.mark.parametrize("question, filename, heading", PARAPHRASES)
def test_paraphrase_finds_expected_source_first(question, filename, heading):
    results = knowledge.search(question)
    ranking = [(r["filename"], r["heading"], r["score"]) for r in results]
    print(question, ranking)
    assert len(results) == 3
    assert (results[0]["filename"], results[0]["heading"]) == (filename, heading), ranking


def test_scores_are_sorted_best_first():
    scores = [r["score"] for r in knowledge.search("Where do donated clothes go?")]
    assert scores == sorted(scores, reverse=True)
