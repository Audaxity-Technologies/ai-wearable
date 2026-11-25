"""Phase D: what a question resolves to.

## The shape of the pipeline

    CLASSIFY  what kind of question is this?
    RESOLVE   names -> person ids, time phrases -> absolute ranges,
              place words -> a place, verbs -> speech acts
    ── ambiguous? ASK. Do not guess. ──
    FILTER    narrow the utterance index to tens of candidates
    ANSWER    cite the spans, or say nothing matched

This module is the contract between the first two steps and the rest. A
`ResolvedQuestion` is a plan, not an answer: it says exactly which filter will
run, which is what makes a wrong answer debuggable rather than merely
plausible.

## Why clarification is a first-class result, not an error

"Which Pulkit — your cofounder, or Pulkit from Infosys?" is a *better* answer
than a confident wrong one, and it is a sentence that makes a user trust the
system more every time they hear it. The failure this prevents is specific: a
wrongly-resolved constraint returns real sentences from the wrong moment or
the wrong person, which reads as an answer rather than as a mistake.

So `Clarification` sits alongside the filters, and a question carrying one is
not executed. The alternative - running the filter minus the ambiguous part -
would quietly widen the search and present the result as if it had been
understood.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.analysis import SpeechAct


class QuestionKind(StrEnum):
    """What the question is asking for, which decides how to answer it."""

    #: "what did we decide about the pitch" - find the thing.
    POINT_LOOKUP = "point_lookup"
    #: "what did we talk about on Friday" - everything in a window.
    TEMPORAL = "temporal"
    #: "what has Pulkit committed to" - everything tied to a person.
    RELATIONSHIP = "relationship"
    #: "how many times did I promise that" - a count, not a list.
    AGGREGATION = "aggregation"
    #: "did I ever mention the deadline" - yes or no, with evidence.
    EXISTENCE = "existence"
    #: Nothing recognisable. Honest, and better than forcing a guess.
    UNKNOWN = "unknown"


class ClarificationKind(StrEnum):
    PERSON = "person"
    TIME = "time"
    PLACE = "place"


class ClarificationOption(BaseModel):
    """One thing the user could have meant."""

    value: str
    label: str
    detail: str = ""


class Clarification(BaseModel):
    """What could not be resolved, and the choices.

    `question` is phrased for a person to read, because this is shown rather
    than logged.
    """

    kind: ClarificationKind
    question: str
    phrase: str = ""
    options: list[ClarificationOption] = Field(default_factory=list)


class ResolvedTimeRange(BaseModel):
    start: dt.datetime
    end: dt.datetime
    label: str


class ResolvedQuestion(BaseModel):
    """A question turned into a filter. Not yet an answer."""

    text: str
    kind: QuestionKind = QuestionKind.UNKNOWN

    # --- resolved constraints ---
    person_ids: list[str] = Field(default_factory=list)
    person_names: list[str] = Field(default_factory=list)
    #: True when the question is about the account holder ("what did I
    #: promise"), which is a different filter from a named person.
    about_self: bool = False

    time_range: ResolvedTimeRange | None = None
    time_phrase: str | None = None

    place: str | None = None

    #: Which speech acts the question implies. Empty means "any", which is
    #: correct for "what did we talk about".
    acts: list[SpeechAct] = Field(default_factory=list)
    #: Only commitments, assignments and requirements - for "what do I owe".
    actionable_only: bool = False

    #: Content words left after the question words, names and time phrases
    #: are removed. What a text filter matches on.
    terms: list[str] = Field(default_factory=list)

    # --- honesty ---
    clarifications: list[Clarification] = Field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarifications)

    @property
    def is_empty(self) -> bool:
        """No constraint at all.

        Worth distinguishing: running an unconstrained filter would return
        the user's entire history and present it as an answer to whatever
        they asked.
        """
        return not any(
            (
                self.person_ids,
                self.about_self,
                self.time_range,
                self.place,
                self.acts,
                self.actionable_only,
                self.terms,
            )
        )

    def describe(self) -> str:
        """The filter in words, so the answer can show its own reasoning."""
        parts: list[str] = []
        if self.acts:
            parts.append("/".join(act.value for act in self.acts))
        elif self.actionable_only:
            parts.append("commitments")
        if self.person_names:
            # "in conversations with", never "by": a participant label
            # records presence, not who spoke which sentence, and the
            # wording the user reads must not overstate it.
            parts.append(
                "in conversations with " + ", ".join(self.person_names)
            )
        elif self.about_self:
            parts.append("by you")
        if self.time_range:
            parts.append(self.time_range.label)
        if self.place:
            parts.append(f"at {self.place}")
        if self.terms:
            parts.append("about " + " ".join(self.terms))
        return "; ".join(parts) if parts else "no constraints"


class QuestionAnswer(BaseModel):
    """The plan, what it matched, and nothing invented.

    `utterances` is deliberately the raw evidence rather than prose. Phase D
    is the retrieval half; composing a sentence from these is the existing
    Ask service's job, and keeping them separate is what stops a generated
    paragraph being mistaken for a citation.
    """

    resolved: ResolvedQuestion
    total: int = 0
    utterances: list[dict] = Field(default_factory=list)
    #: Said plainly when the filter matched nothing, rather than returning an
    #: empty list and letting the caller imply an answer.
    verdict: str = ""
