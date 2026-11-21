"""Eval harness for the extraction prompt.

Run this before and after any change to `services/llm/prompts.py` or the
analysis service. Without it, prompt edits regress owner attribution and
speech-act classification silently, and you find out when a user is told they
promised something they never said.

Scoring is deliberately strict on the things that matter:

- **recall**     did we find the item at all?
- **act**        is the speech act right? (commitment vs suggestion vs ...)
- **owner**      is the owner right? This is the costliest error.
- **date**       is the resolved date right, AND did we correctly refuse to
                 resolve an ambiguous one?
- **spurious**   items we extracted that no label covers (over-extraction).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.errors import LLMError
from app.core.text import normalise, quote_similarity
from app.models.analysis import AnalysisItem, ConversationAnalysis
from app.models.domain import Session
from app.services.analysis import AnalysisService

CASES_DIR = Path(__file__).parent / "cases"

#: Minimum quote similarity for an extracted item to count as the labelled one.
MATCH_THRESHOLD = 0.6


@dataclass
class ItemScore:
    quote: str
    found: bool
    act_ok: bool = False
    owner_ok: bool = False
    date_ok: bool = False
    expected: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseScore:
    case_id: str
    description: str
    items: list[ItemScore] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)
    #: Extractions we have decided to accept: borderline cases where a
    #: deliberate product choice makes them defensible. Reported, not failed.
    tolerated: list[str] = field(default_factory=list)
    #: Extractions a case explicitly forbids via `must_not_extract`. Checked
    #: against EVERY extracted item, including ones that matched a label, so
    #: an item cannot slip through by resembling something expected.
    forbidden: list[str] = field(default_factory=list)
    #: Spans a case requires us to have suppressed as media
    #: (`must_suppress_as_media`) and that we did not. Rule 13.
    unsuppressed: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None
    repair_attempted: bool = False

    @property
    def expected_count(self) -> int:
        return len(self.items)

    @property
    def found_count(self) -> int:
        return sum(1 for item in self.items if item.found)

    @property
    def act_correct(self) -> int:
        return sum(1 for item in self.items if item.act_ok)

    @property
    def owner_correct(self) -> int:
        return sum(1 for item in self.items if item.owner_ok)

    @property
    def date_correct(self) -> int:
        return sum(1 for item in self.items if item.date_ok)

    @property
    def passed(self) -> bool:
        return (
            not self.degraded
            and self.found_count == self.expected_count
            and self.act_correct == self.expected_count
            and self.owner_correct == self.expected_count
            and self.date_correct == self.expected_count
            and not self.spurious
            and not self.forbidden
            and not self.unsuppressed
        )


def load_cases(case_id: str | None = None) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if case_id is None or data["id"] == case_id:
            cases.append(data)
    return cases


def session_from_case(case: dict[str, Any]) -> Session:
    return Session(
        user_id="eval",
        tz=case["tz"],
        started_at=datetime.fromisoformat(case["recorded_at"]),
        transcript=case["transcript"],
    )


def _expected_acts(expected: dict[str, Any]) -> list[str]:
    """Acts a label accepts.

    `act_any` exists for utterances that are genuinely ambiguous in English -
    "Let's meet tomorrow", accepted with "Okay", is defensibly a suggestion or
    a decision. Forcing one answer would make the scorecard a coin-flip and
    hide real regressions in the noise.
    """
    if expected.get("act_any"):
        return list(expected["act_any"])
    return [expected["act"]]


def _owner_matches(expected: str, actual: str) -> bool:
    """Owner comparison, case-insensitive and tolerant of first-name-only."""
    expected_clean = expected.strip().lower()
    actual_clean = actual.strip().lower()
    if expected_clean == actual_clean:
        return True
    # "manan" should match "Manan Dudeja", but never match "unknown".
    if expected_clean in {"unknown", "group"} or actual_clean in {
        "unknown",
        "group",
    }:
        return False
    return expected_clean in actual_clean or actual_clean in expected_clean


def _date_matches(expected: dict[str, Any], item: AnalysisItem) -> bool:
    expected_resolution = expected.get("due_resolution")
    expected_date = expected.get("due_date")

    if expected_resolution and item.due.resolution.value != expected_resolution:
        return False
    if expected_date:
        if item.due.date is None:
            return False
        if item.due.date.isoformat() != expected_date:
            return False
    elif expected_resolution in {"ambiguous", "none"}:
        # Refusing to invent a date is the point: a concrete date here is a
        # failure even though the resolution label matched.
        if item.due.date is not None and expected_resolution == "ambiguous":
            # Some ambiguous phrases legitimately carry a date hint (e.g.
            # "next Friday"), so only a *confident* date would be wrong.
            return item.due.resolution.value == "ambiguous"
    expected_time = expected.get("due_time")
    if expected_time:
        if item.due.time is None:
            return False
        if item.due.time.strftime("%H:%M") != expected_time:
            return False
    return True


def score_case(
    case: dict[str, Any], analysis: ConversationAnalysis
) -> CaseScore:
    score = CaseScore(
        case_id=case["id"],
        description=case["description"],
        degraded=analysis.degraded,
        degraded_reason=analysis.degraded_reason,
        repair_attempted=analysis.repair_attempted,
        dropped=[f"{d.title} ({d.reason})" for d in analysis.dropped],
    )

    unmatched = list(analysis.items)
    for expected in case["expected_items"]:
        best: AnalysisItem | None = None
        best_ratio = 0.0
        for candidate in unmatched:
            ratio = max(
                quote_similarity(expected["quote"], candidate.source_quote),
                quote_similarity(candidate.source_quote, expected["quote"]),
            )
            if ratio > best_ratio:
                best_ratio, best = ratio, candidate

        if best is None or best_ratio < MATCH_THRESHOLD:
            score.items.append(ItemScore(quote=expected["quote"], found=False,
                                         expected=expected))
            continue

        unmatched.remove(best)
        score.items.append(
            ItemScore(
                quote=expected["quote"],
                found=True,
                act_ok=best.act.value in _expected_acts(expected),
                owner_ok=_owner_matches(expected["owner"], best.owner),
                date_ok=_date_matches(expected, best),
                expected=expected,
                actual={
                    "title": best.title,
                    "act": best.act.value,
                    "owner": best.owner,
                    "due": best.due.model_dump(mode="json"),
                },
            )
        )

    # `must_not_extract_spans` is checked against every extracted item,
    # matched or not. A forbidden fragment that happens to resemble a labelled
    # quote would otherwise be consumed by the matcher and never reported -
    # which is how `remind_me`'s stated "real assertion" went unchecked.
    #
    # Note the two distinct keys, because the corpus uses both:
    #   `must_not_extract`       - PROSE for a human reader ("a confirmed
    #                              meeting rather than a proposed one").
    #                              Never matched against anything.
    #   `must_not_extract_spans` - VERBATIM transcript spans, enforced here.
    # Conflating them would make every prose note a silently-passing
    # assertion, which is the failure this check exists to end.
    for pattern in case.get("must_not_extract_spans", []):
        needle = normalise(pattern)
        if not needle:
            continue
        for item in analysis.items:
            if needle in normalise(f"{item.title} {item.source_quote}"):
                score.forbidden.append(f"{pattern!r} -> {item.title}")
                break

    # `must_suppress_as_media` asserts the inverse of over-extraction: the
    # span must appear in `dropped` with a media reason. Checking only that it
    # is absent from `items` would pass for the wrong reason - the model
    # simply ignoring that part of the transcript.
    media_dropped = [
        entry
        for entry in analysis.dropped
        if entry.reason.startswith("media")
    ]
    for pattern in case.get("must_suppress_as_media", []):
        needle = normalise(pattern)
        if not needle:
            continue
        if not any(
            needle in normalise(entry.source_quote or "")
            for entry in media_dropped
        ):
            score.unsuppressed.append(pattern)

    tolerated_patterns = [
        normalise(pattern) for pattern in case.get("tolerated_spurious", [])
    ]
    for item in unmatched:
        label = f"{item.title} [{item.act.value}]"
        haystack = normalise(f"{item.title} {item.source_quote}")
        if any(pattern and pattern in haystack for pattern in tolerated_patterns):
            score.tolerated.append(label)
        else:
            score.spurious.append(label)
    return score


async def run_case(
    case: dict[str, Any],
    service: AnalysisService,
    *,
    max_retries: int = 4,
) -> CaseScore:
    """Run one case, backing off on rate limits.

    Groq's free tier is 8000 tokens/minute and one analysis call costs roughly
    2000, so a four-case run will hit 429 unless it paces itself. Backoff here
    keeps the eval usable on the free tier.
    """
    delay = 5.0
    for attempt in range(max_retries):
        try:
            analysis = await service.analyze(session_from_case(case))
            return score_case(case, analysis)
        except LLMError as exc:
            if not exc.retryable or attempt == max_retries - 1:
                raise
            print(
                f"  rate limited, waiting {delay:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def format_report(scores: list[CaseScore]) -> str:
    lines: list[str] = []
    total = correct = acts = owners = dates = found = spurious = 0
    tolerated = forbidden = unsuppressed = 0

    for score in scores:
        status = "PASS" if score.passed else "FAIL"
        lines.append(f"\n[{status}] {score.case_id} - {score.description}")
        if score.degraded:
            lines.append(f"  DEGRADED: {score.degraded_reason}")
        if score.repair_attempted:
            lines.append("  (repair attempt was needed)")

        for item in score.items:
            if not item.found:
                lines.append(f"  MISSED   {item.quote[:70]!r}")
                continue
            flags = []
            if not item.act_ok:
                flags.append(
                    f"act: expected {'|'.join(_expected_acts(item.expected))}, "
                    f"got {item.actual['act']}"
                )
            if not item.owner_ok:
                flags.append(
                    f"OWNER: expected {item.expected['owner']}, "
                    f"got {item.actual['owner']}"
                )
            if not item.date_ok:
                flags.append(
                    f"date: expected {item.expected.get('due_date') or ''}"
                    f"/{item.expected.get('due_resolution')}, "
                    f"got {item.actual['due'].get('date')}"
                    f"/{item.actual['due'].get('resolution')}"
                )
            marker = "ok   " if not flags else "WRONG"
            lines.append(f"  {marker}    {item.quote[:60]!r}")
            for flag in flags:
                lines.append(f"           -> {flag}")

        for extra in score.spurious:
            lines.append(f"  SPURIOUS {extra}")
        for bad in score.forbidden:
            lines.append(f"  FORBIDDEN {bad}")
        for miss in score.unsuppressed:
            lines.append(f"  NOT SUPPRESSED {miss[:70]!r}")
        for extra in score.tolerated:
            lines.append(f"  accepted {extra}")
        for drop in score.dropped:
            lines.append(f"  dropped  {drop}")

        total += score.expected_count
        found += score.found_count
        acts += score.act_correct
        owners += score.owner_correct
        dates += score.date_correct
        spurious += len(score.spurious)
        tolerated += len(score.tolerated)
        forbidden += len(score.forbidden)
        unsuppressed += len(score.unsuppressed)
        correct += 1 if score.passed else 0

    def pct(numerator: int) -> str:
        return f"{numerator}/{total} ({100 * numerator / total:.0f}%)" if total else "n/a"

    lines.append("\n" + "=" * 60)
    lines.append(f"cases passed    {correct}/{len(scores)}")
    lines.append(f"items found     {pct(found)}")
    lines.append(f"act correct     {pct(acts)}")
    lines.append(f"owner correct   {pct(owners)}   <- the costliest error")
    lines.append(f"date correct    {pct(dates)}")
    lines.append(f"spurious items  {spurious}   <- over-extraction")
    if forbidden:
        lines.append(
            f"forbidden       {forbidden}   <- must_not_extract violated"
        )
    if unsuppressed:
        lines.append(
            f"not suppressed  {unsuppressed}   <- media leaked through (rule 13)"
        )
    if tolerated:
        lines.append(
            f"accepted extras {tolerated}   <- borderline, decided deliberately"
        )
    lines.append("=" * 60)
    return "\n".join(lines)
