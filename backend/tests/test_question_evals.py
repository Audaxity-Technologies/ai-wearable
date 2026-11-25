"""The retrieval eval set, run as ordinary tests (memory phase G).

This exists as a test file and not only as a script because phase D is
deterministic: no LLM call, so a case costs nothing and cannot flake. The
extraction eval has to be run deliberately and paced against rate limits;
this one runs on every commit, which is where a retrieval regression will
actually get caught.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import pytest

from app.core.text import verify_quote
from evals.question_runner import (
    build_world,
    load_cases,
    load_corpus,
    run_case,
)

CASES = load_cases()["cases"]
CASE_IDS = [case["id"] for case in CASES]


# --- meta-tests on the corpus and labels --------------------------------


def test_the_corpus_is_small_enough_to_reason_about() -> None:
    """A corpus you cannot check by hand produces failures you cannot
    diagnose."""
    corpus = load_corpus()
    spans = sum(len(s["spans"]) for s in corpus["sessions"])
    assert spans <= 20, "keep the corpus hand-checkable"
    assert len(corpus["sessions"]) >= 3


def test_every_expected_citation_exists_in_the_corpus() -> None:
    """A citation label that is not in the corpus would score the parser
    against fiction - the same failure the extraction eval's quote check
    guards against."""
    corpus = load_corpus()
    haystack = " ".join(
        row["text"] for s in corpus["sessions"] for row in s["spans"]
    )
    for case in CASES:
        for text in case.get("expect", {}).get("texts", []):
            assert verify_quote(text, haystack), (
                f"{case['id']}: expected citation not in corpus: {text!r}"
            )


def test_the_fixed_now_is_timezone_aware() -> None:
    """A naive `now` would resolve days in whatever zone the machine runs in,
    and the suite would answer about a different day abroad."""
    now = dt.datetime.fromisoformat(load_cases()["now"])
    assert now.tzinfo is not None


def test_cases_cover_the_question_kinds() -> None:
    """Including the refusals, which are the behaviours most likely to be
    quietly lost in a refactor."""
    kinds = {
        case.get("expect", {}).get("kind")
        for case in CASES
        if case.get("expect", {}).get("kind")
    }
    assert {"temporal", "relationship", "existence", "aggregation", "unknown"} <= kinds
    assert any("clarification" in case.get("expect", {}) for case in CASES)
    assert any(case.get("expect", {}).get("total") == 0 for case in CASES)


# --- the cases themselves -----------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
async def test_question_resolves_and_cites(case: dict) -> None:
    with tempfile.TemporaryDirectory() as raw:
        parser, user, now = await build_world(Path(raw))
        score = await run_case(case, parser, user, now)

    failures = [
        f"{name}: expected {expected!r}, got {actual!r}"
        for name, ok, expected, actual in score.checks
        if not ok
    ]
    assert not failures, f"{case['id']}: " + "; ".join(failures)
    assert not score.missing_citations, (
        f"{case['id']}: not cited: {score.missing_citations}"
    )
