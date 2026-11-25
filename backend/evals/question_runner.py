"""Retrieval eval harness (memory phase G).

Run before and after any change to `services/questions.py`, `core/when.py` or
the utterance linking. Retrieval has strictly more ways to degrade silently
than extraction does: a filter can resolve wrongly and still return plausible
sentences, which reads as an answer.

## Why this one runs in pytest and the extraction eval does not

Phase D is deterministic - no LLM call - so a case costs nothing and cannot
flake. That is a direct benefit of the rule-based parser: this set runs on
every commit, where the extraction eval has to be run deliberately and paced
against Groq's rate limits.

## Resolution and citations are scored separately

A case can resolve the right filter and still cite the wrong spans. Those are
different bugs - a parser fault versus an index fault - and one number would
hide which. The report prints both.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.text import normalise
from app.models.analysis import SpeechAct
from app.models.domain import (
    Participation,
    ParticipationRole,
    Segment,
    Session,
    TranscriptSpan,
)
from app.models.memory import Memory
from app.models.people import Person
from app.models.places import CoarsePlace
from app.models.tasks import Task
from app.models.utterances import Utterance
from app.repositories.json_repo import JsonRepository
from app.services.people import PersonService
from app.services.questions import QuestionParser
from app.services.utterances import UtteranceService

HERE = Path(__file__).parent / "questions"


def load_corpus() -> dict[str, Any]:
    return json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))


def load_cases() -> dict[str, Any]:
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


@dataclass
class CaseScore:
    case_id: str
    question: str
    #: Field-level resolution results: (field, ok, expected, actual).
    checks: list[tuple[str, bool, Any, Any]] = field(default_factory=list)
    cited_found: int = 0
    cited_expected: int = 0
    missing_citations: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def resolution_ok(self) -> bool:
        return all(ok for _, ok, _, _ in self.checks)

    @property
    def citations_ok(self) -> bool:
        return self.cited_found == self.cited_expected

    @property
    def passed(self) -> bool:
        return self.resolution_ok and self.citations_ok


async def build_world(tmp: Path) -> tuple[QuestionParser, str, dt.datetime]:
    """Seed the fixed corpus into throwaway repositories.

    Built through `UtteranceService.rebuild_for_session` rather than by
    writing rows directly, so the eval exercises the real builder - including
    the absolute-time arithmetic, which is the part most likely to be wrong.
    """
    corpus = load_corpus()
    cases = load_cases()
    user = corpus["user_id"]
    now = dt.datetime.fromisoformat(cases["now"])

    people_repo = JsonRepository(tmp / "people.json", Person)
    sessions_repo = JsonRepository(tmp / "sessions.json", Session)
    utter_repo = JsonRepository(tmp / "utterances.json", Utterance)
    people = PersonService(people=people_repo)
    utterances = UtteranceService(utterances=utter_repo)

    for entry in corpus.get("people", []):
        person = await people.resolve_or_create(user, entry["name"])
        assert person is not None
        for alias in entry.get("aliases", []):
            await people.add_alias(person.id, alias)
    if corpus.get("self_person"):
        me = await people.resolve_or_create(user, corpus["self_person"])
        assert me is not None
        await people.set_self(me.id, True)

    for conversation in corpus["sessions"]:
        started = dt.datetime.fromisoformat(conversation["started_at"])
        session = Session(
            id=conversation["id"],
            user_id=user,
            tz=corpus["tz"],
            started_at=started,
        )
        place = (
            CoarsePlace(lat=28.5, lon=77.1, label=conversation["place"])
            if conversation.get("place")
            else None
        )
        spans = [
            TranscriptSpan(
                start_s=index * 10.0, end_s=index * 10.0 + 8.0, text=row["text"]
            )
            for index, row in enumerate(conversation["spans"])
        ]
        segment = Segment(
            id=f"seg-{conversation['id']}",
            session_id=session.id,
            user_id=user,
            seq=0,
            started_at=started,
            duration_s=len(spans) * 10.0,
            mime_type="audio/m4a",
            size_bytes=1024,
            blob_key=f"blob-{conversation['id']}",
            spans=spans,
            place=place,
        )
        # Participant labels, applied the way a user would: resolved
        # through the people index, so an alias lands on the same person.
        for name in conversation.get("participants", []):
            person = await people.resolve_or_create(
                user, name, session_id=session.id, at=started
            )
            assert person is not None
            session.participants.append(
                Participation(
                    person_id=person.id,
                    name=person.canonical_name,
                    role=ParticipationRole.SPOKE,
                )
            )
        await sessions_repo.put(session)

        built = await utterances.rebuild_for_session(session, [segment])
        # Attach the labelled acts directly. The linking rules are the
        # utterance eval's business; here the acts are given, so a retrieval
        # failure is never really a linking failure in disguise.
        for utterance, row in zip(built, conversation["spans"], strict=True):
            if row.get("act"):
                utterance.act = SpeechAct(row["act"])
                utterance.item_owner = row.get("owner")
                await utter_repo.put(utterance)

    parser = QuestionParser(
        people=people, utterances=utterances, sessions=sessions_repo
    )
    return parser, user, now


async def run_case(
    case: dict[str, Any], parser: QuestionParser, user: str, now: dt.datetime
) -> CaseScore:
    expect = case.get("expect", {})
    score = CaseScore(
        case_id=case["id"], question=case["question"], note=case.get("note", "")
    )
    answer = await parser.answer(
        case["question"], user_id=user, tz="Asia/Kolkata", now=now, limit=50
    )
    resolved = answer.resolved

    def check(name: str, expected: Any, actual: Any) -> None:
        score.checks.append((name, expected == actual, expected, actual))

    if "kind" in expect:
        check("kind", expect["kind"], resolved.kind.value)
    if "acts" in expect:
        check("acts", sorted(expect["acts"]), sorted(a.value for a in resolved.acts))
    if "about_self" in expect:
        check("about_self", expect["about_self"], resolved.about_self)
    if "actionable_only" in expect:
        check("actionable_only", expect["actionable_only"], resolved.actionable_only)
    if "place" in expect:
        check("place", expect["place"], resolved.place)
    if "time_label" in expect:
        check(
            "time_label",
            expect["time_label"],
            resolved.time_range.label if resolved.time_range else None,
        )
    if "person_names" in expect:
        check("person_names", sorted(expect["person_names"]), sorted(resolved.person_names))
    if "terms" in expect:
        for term in expect["terms"]:
            score.checks.append(
                (f"term:{term}", term in resolved.terms, True, term in resolved.terms)
            )
    if "not_terms" in expect:
        for term in expect["not_terms"]:
            score.checks.append(
                (
                    f"not_term:{term}",
                    term not in resolved.terms,
                    False,
                    term in resolved.terms,
                )
            )
    if "clarification" in expect:
        kinds = [c.kind.value for c in resolved.clarifications]
        score.checks.append(
            (
                "clarification",
                expect["clarification"] in kinds,
                expect["clarification"],
                kinds,
            )
        )
    if "total" in expect:
        check("total", expect["total"], answer.total)
    if "verdict_contains" in expect:
        needle = expect["verdict_contains"].lower()
        score.checks.append(
            (
                "verdict",
                needle in answer.verdict.lower(),
                needle,
                answer.verdict[:60],
            )
        )

    wanted = expect.get("texts", [])
    score.cited_expected = len(wanted)
    returned = [normalise(row["text"]) for row in answer.utterances]
    for text in wanted:
        if any(normalise(text) in row for row in returned):
            score.cited_found += 1
        else:
            score.missing_citations.append(text)

    return score


def format_report(scores: list[CaseScore]) -> str:
    lines: list[str] = []
    resolution_checks = resolution_ok = 0
    cited = cited_total = passed = 0

    for score in scores:
        status = "PASS" if score.passed else "FAIL"
        lines.append(f"\n[{status}] {score.case_id}  {score.question!r}")
        for name, ok, expected, actual in score.checks:
            resolution_checks += 1
            resolution_ok += 1 if ok else 0
            if not ok:
                lines.append(
                    f"  {name}: expected {expected!r}, got {actual!r}"
                )
        for text in score.missing_citations:
            lines.append(f"  NOT CITED {text[:64]!r}")
        cited += score.cited_found
        cited_total += score.cited_expected
        passed += 1 if score.passed else 0

    def pct(part: int, whole: int) -> str:
        return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "n/a"

    lines.append("\n" + "=" * 60)
    lines.append(f"cases passed       {passed}/{len(scores)}")
    lines.append(f"resolution checks  {pct(resolution_ok, resolution_checks)}")
    lines.append(f"citations found    {pct(cited, cited_total)}")
    lines.append("=" * 60)
    return "\n".join(lines)


async def run_all() -> list[CaseScore]:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        parser, user, now = await build_world(tmp)
        return [
            await run_case(case, parser, user, now)
            for case in load_cases()["cases"]
        ]


__all__ = [
    "CaseScore",
    "build_world",
    "format_report",
    "load_cases",
    "load_corpus",
    "run_all",
    "run_case",
]
