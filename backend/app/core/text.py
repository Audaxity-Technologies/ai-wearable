"""Text normalisation and quote verification.

The "nothing invented" rule is enforced here: an extracted item whose
`source_quote` cannot be found in the transcript is dropped before it reaches
storage. That single check removes most hallucinated tasks, and it is what
lets the app show "why did you create this?" on every card.

Exact matching is too strict in practice - models normalise punctuation,
capitalisation and contractions - so matching is done on normalised text with a
bounded fuzzy fallback.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

#: Below this similarity a quote is treated as not present in the transcript.
SIMILARITY_THRESHOLD = 0.85

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    lowered = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    stripped = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", stripped).strip()


def quote_similarity(quote: str, transcript: str) -> float:
    """Best similarity between `quote` and any same-length window of text.

    Returns 1.0 on an exact (normalised) substring match.
    """
    needle = normalise(quote)
    haystack = normalise(transcript)
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0

    needle_words = needle.split()
    haystack_words = haystack.split()
    window = len(needle_words)
    if window == 0 or window > len(haystack_words):
        # Quote is longer than the transcript: compare whole strings.
        return SequenceMatcher(None, needle, haystack).ratio()

    best = 0.0
    # Slide a window of the quote's length across the transcript. Bounded by
    # transcript length, which is a single conversation, so this stays cheap.
    for start in range(len(haystack_words) - window + 1):
        candidate = " ".join(haystack_words[start : start + window])
        ratio = SequenceMatcher(None, needle, candidate).ratio()
        if ratio > best:
            best = ratio
            if best >= 0.99:
                break
    return best


def verify_quote(quote: str, transcript: str) -> bool:
    """True if `quote` plausibly appears in `transcript`."""
    if not quote.strip():
        return False
    return quote_similarity(quote, transcript) >= SIMILARITY_THRESHOLD


def extract_json_object(text: str) -> str:
    """Pull the JSON object out of a model response.

    Handles code fences and stray prose around the object, which models emit
    even when asked not to.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # ```json\n{...}\n```
        fenced = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        fenced = re.sub(r"\s*```$", "", fenced)
        stripped = fenced.strip()

    if stripped.startswith("{"):
        return stripped

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped
