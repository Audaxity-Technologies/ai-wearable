"""Quote verification: the 'nothing invented' guard."""

from __future__ import annotations

from app.core.text import extract_json_object, normalise, verify_quote

TRANSCRIPT = (
    "We need to finish the investor pitch by Friday. "
    "I'll work on the financial projections. "
    "Let's meet tomorrow at 5 PM and review the wearable prototype."
)


def test_exact_quote_verifies() -> None:
    assert verify_quote("I'll work on the financial projections.", TRANSCRIPT)


def test_punctuation_and_case_differences_still_verify() -> None:
    """Models normalise punctuation; that must not count as a hallucination."""
    assert verify_quote("ill work on the financial projections", TRANSCRIPT)
    assert verify_quote("I'LL WORK ON THE FINANCIAL PROJECTIONS", TRANSCRIPT)
    assert verify_quote("I’ll work on the financial projections", TRANSCRIPT)


def test_invented_quote_is_rejected() -> None:
    assert not verify_quote(
        "I'll call the lawyer about the trademark on Monday.", TRANSCRIPT
    )


def test_paraphrase_is_rejected() -> None:
    """A paraphrase is not a quote, even when it is a fair summary."""
    assert not verify_quote(
        "Manan agreed to handle the financial modelling work", TRANSCRIPT
    )


def test_empty_quote_is_rejected() -> None:
    assert not verify_quote("", TRANSCRIPT)
    assert not verify_quote("   ", TRANSCRIPT)


def test_normalise() -> None:
    assert normalise("  Hello,   World!  ") == "hello world"


def test_extract_json_from_code_fence() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json_object('```\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_from_surrounding_prose() -> None:
    text = 'Sure! Here is the JSON:\n{"a": 1}\nHope that helps.'
    assert extract_json_object(text) == '{"a": 1}'


def test_extract_json_passes_through_clean_json() -> None:
    assert extract_json_object('{"a": 1}') == '{"a": 1}'
