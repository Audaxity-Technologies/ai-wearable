"""Analysis service: schema, repair, quote verification, date resolution."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.time_utils import utc_now
from app.models.analysis import (
    DateResolution,
    SpeechAct,
)
from app.models.domain import Session
from app.services.analysis import AnalysisService
from app.services.llm.fake_driver import DEFAULT_ANALYSIS, FakeLLMDriver

IST = ZoneInfo("Asia/Kolkata")

TRANSCRIPT = (
    "We need to finish the investor pitch by Friday. "
    "I'll work on the financial projections. "
    "Let's meet tomorrow at 5 PM and review the wearable prototype. "
    "We should contact the V-NEST team next week about the incubation update."
)


def make_session(transcript: str = TRANSCRIPT) -> Session:
    return Session(
        user_id="manan",
        tz="Asia/Kolkata",
        # A Thursday, 17:00 IST.
        started_at=datetime(2026, 11, 19, 11, 30, tzinfo=ZoneInfo("UTC")),
        transcript=transcript,
    )


def analysis_payload(**overrides) -> str:
    payload = json.loads(json.dumps(DEFAULT_ANALYSIS))
    payload.update(overrides)
    return json.dumps(payload)


async def test_happy_path_extracts_items() -> None:
    service = AnalysisService(driver=FakeLLMDriver())
    result = await service.analyze(make_session())

    assert not result.degraded
    assert result.summary
    assert {p.name for p in result.participants} == {"Pulkit", "Manan"}
    assert len(result.items) == 4
    assert result.dropped == []


async def test_speech_acts_are_preserved() -> None:
    """The product's core distinction: who actually committed to what."""
    service = AnalysisService(driver=FakeLLMDriver())
    result = await service.analyze(make_session())
    by_title = {item.title: item for item in result.items}

    commitment = by_title["Work on the financial projections"]
    assert commitment.act is SpeechAct.COMMITMENT
    assert commitment.owner == "Manan"
    assert commitment.is_actionable

    requirement = by_title["Finish the investor pitch"]
    assert requirement.act is SpeechAct.REQUIREMENT
    assert requirement.owner == "group"

    suggestion = by_title["Contact the V-NEST team"]
    assert suggestion.act is SpeechAct.SUGGESTION
    # A suggestion must not silently become someone's task.
    assert not suggestion.is_actionable


async def test_dates_are_resolved_from_the_session_anchor() -> None:
    service = AnalysisService(driver=FakeLLMDriver())
    result = await service.analyze(make_session())
    by_title = {item.title: item for item in result.items}

    # Recorded Thursday 2026-11-19 IST.
    pitch = by_title["Finish the investor pitch"]
    assert pitch.due.raw == "by Friday"
    assert pitch.due.date is not None
    assert pitch.due.date.isoformat() == "2026-11-20"
    assert pitch.due.resolution is DateResolution.CONFIDENT

    review = by_title["Review the wearable prototype"]
    assert review.due.date is not None
    assert review.due.date.isoformat() == "2026-11-20"
    assert review.due.time is not None
    assert review.due.time.hour == 17

    vnest = by_title["Contact the V-NEST team"]
    assert vnest.due.raw == "next week"
    assert vnest.due.date is None
    assert vnest.due.needs_clarification


async def test_item_without_a_verifiable_quote_is_dropped() -> None:
    """The hallucination guard. An unquotable item never reaches storage."""
    payload = json.loads(json.dumps(DEFAULT_ANALYSIS))
    payload["items"].append(
        {
            "title": "Call the lawyer about the trademark",
            "act": "commitment",
            "owner": "Pulkit",
            "source_quote": "I'll call the lawyer about the trademark Monday.",
            "due_raw": "Monday",
            "project": None,
            "confidence": 0.9,
        }
    )
    service = AnalysisService(driver=FakeLLMDriver(responses=[json.dumps(payload)]))
    result = await service.analyze(make_session())

    titles = {item.title for item in result.items}
    assert "Call the lawyer about the trademark" not in titles
    assert len(result.dropped) == 1
    assert result.dropped[0].reason == "source_quote not found in transcript"


async def test_duplicate_items_are_dropped() -> None:
    payload = json.loads(json.dumps(DEFAULT_ANALYSIS))
    payload["items"].append(dict(payload["items"][1]))
    service = AnalysisService(driver=FakeLLMDriver(responses=[json.dumps(payload)]))
    result = await service.analyze(make_session())

    assert len(result.items) == 4
    assert any("duplicate" in item.reason for item in result.dropped)


async def test_blank_owner_becomes_unknown() -> None:
    """Never invent an owner; 'unknown' is the correct answer."""
    payload = json.loads(json.dumps(DEFAULT_ANALYSIS))
    payload["items"][1]["owner"] = ""
    service = AnalysisService(driver=FakeLLMDriver(responses=[json.dumps(payload)]))
    result = await service.analyze(make_session())

    item = next(
        i for i in result.items if i.title == "Work on the financial projections"
    )
    assert item.owner == "unknown"


async def test_malformed_json_triggers_one_repair_attempt() -> None:
    driver = FakeLLMDriver(
        responses=["this is not json at all", json.dumps(DEFAULT_ANALYSIS)]
    )
    service = AnalysisService(driver=driver)
    result = await service.analyze(make_session())

    assert len(driver.calls) == 2, "exactly one repair attempt"
    assert not result.degraded
    assert result.repair_attempted
    assert len(result.items) == 4
    # The repair prompt must carry the validator error back to the model.
    assert "could not be parsed" in driver.calls[1][1]


async def test_schema_violation_triggers_repair() -> None:
    bad = json.dumps({"title": "x", "items": [{"title": "y", "act": "invented"}]})
    driver = FakeLLMDriver(responses=[bad, json.dumps(DEFAULT_ANALYSIS)])
    result = await AnalysisService(driver=driver).analyze(make_session())

    assert len(driver.calls) == 2
    assert not result.degraded


async def test_repair_failure_degrades_instead_of_crashing() -> None:
    """A bad model response must never cost the user their conversation."""
    driver = FakeLLMDriver(responses=["nonsense", "still nonsense"])
    result = await AnalysisService(driver=driver).analyze(make_session())

    assert result.degraded
    assert result.degraded_reason
    assert result.items == []
    assert result.session_id


async def test_code_fenced_response_is_accepted_without_repair() -> None:
    driver = FakeLLMDriver(
        responses=[f"```json\n{json.dumps(DEFAULT_ANALYSIS)}\n```"]
    )
    result = await AnalysisService(driver=driver).analyze(make_session())

    assert len(driver.calls) == 1, "fenced JSON should not need repairing"
    assert not result.degraded


async def test_short_transcript_skips_the_llm_entirely() -> None:
    driver = FakeLLMDriver()
    result = await AnalysisService(driver=driver).analyze(make_session("Hi."))

    assert driver.calls == [], "no quota spent on a two-word transcript"
    assert result.degraded


async def test_prompt_includes_the_recording_date_and_timezone() -> None:
    driver = FakeLLMDriver()
    await AnalysisService(driver=driver).analyze(make_session())

    _system, user = driver.calls[0]
    assert "2026-11-19" in user
    assert "Thursday" in user
    assert "Asia/Kolkata" in user
    assert TRANSCRIPT in user


async def test_analysis_id_is_the_session_id() -> None:
    session = make_session()
    result = await AnalysisService(driver=FakeLLMDriver()).analyze(session)
    assert result.id == session.id == result.session_id
