"""Phase D: parsing a question into a filter over the utterance index.

See `app/models/questions.py` for the pipeline and why clarification is a
first-class result.

## Deterministic, deliberately

No LLM call. Classification is keyword rules, time resolution is
`core/when.py`, names come from `PersonService`'s alias matching, and places
come from the places actually present in the index.

That is a considered choice rather than laziness. The failure mode this
product cannot afford is a confident wrong answer, and a rule-based parser
fails *visibly*: when it does not understand a question it says so, and when
it resolves one you can read exactly which filter ran. An LLM classifier
would handle more phrasings and would occasionally resolve "what did Priya
say" into a filter on Prithvi, with nothing in the output to show it had.

The honest limit: phrasings outside the rules return `UNKNOWN` rather than a
guess. When the retrieval eval set (phase G) shows which real questions fall
through, that is the evidence for adding an LLM pass *behind* this one -
measured against the rules, not instead of them.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.core.logging import get_logger, log_event
from app.core.text import normalise
from app.core.time_utils import utc_now
from app.core.when import TimeRange, resolve_when
from app.models.analysis import SpeechAct
from app.models.questions import (
    Clarification,
    ClarificationKind,
    ClarificationOption,
    QuestionAnswer,
    QuestionKind,
    ResolvedQuestion,
    ResolvedTimeRange,
)
from app.models.utterances import UtteranceResponse
from app.services.people import PersonService
from app.services.utterances import UtteranceService

logger = get_logger(__name__)

#: Verbs that name a speech act. The product's core distinction, so the
#: mapping is explicit rather than inferred.
ACT_WORDS: dict[str, SpeechAct] = {
    "decide": SpeechAct.DECISION,
    "decided": SpeechAct.DECISION,
    "decision": SpeechAct.DECISION,
    "agreed": SpeechAct.DECISION,
    "promise": SpeechAct.COMMITMENT,
    "promised": SpeechAct.COMMITMENT,
    "commit": SpeechAct.COMMITMENT,
    "committed": SpeechAct.COMMITMENT,
    "commitment": SpeechAct.COMMITMENT,
    "supposed": SpeechAct.ASSIGNMENT,
    "asked": SpeechAct.ASSIGNMENT,
    "assigned": SpeechAct.ASSIGNMENT,
    "suggest": SpeechAct.SUGGESTION,
    "suggested": SpeechAct.SUGGESTION,
    "suggestion": SpeechAct.SUGGESTION,
    "propose": SpeechAct.SUGGESTION,
    "proposed": SpeechAct.SUGGESTION,
    "agree": SpeechAct.DECISION,
    "assign": SpeechAct.ASSIGNMENT,
    "ask": SpeechAct.ASSIGNMENT,
}

#: Phrases meaning "anything owed", which spans three acts rather than one.
OWING_WORDS = frozenset({"owe", "owes", "owed", "outstanding", "pending", "todo"})

AGGREGATION_WORDS = frozenset(
    {"how many", "how often", "how much", "count", "number of"}
)
EXISTENCE_WORDS = frozenset(
    {"did i ever", "have i ever", "did we ever", "have we ever", "did anyone ever"}
)

#: First-person references, which mean the account holder rather than a name.
SELF_WORDS = frozenset({"i", "me", "my", "myself", "mine"})

#: Dropped before text matching: question scaffolding carries no content.
STOPWORDS = frozenset(
    {
        "what", "when", "where", "who", "whom", "whose", "why", "how", "which",
        "did", "do", "does", "was", "were", "is", "are", "am", "been", "be",
        "we", "us", "our", "you", "your", "they", "them", "their", "he", "she",
        "it", "the", "a", "an", "and", "or", "but", "of", "to", "for", "in",
        "on", "at", "by", "with", "about", "from", "that", "this", "these",
        "those", "there", "here", "say", "said", "tell", "told", "talk",
        "talked", "mention", "mentioned", "discuss", "discussed", "anything",
        "something", "ever", "again", "me", "i", "my", "any", "all", "some",
        # Indefinite pronouns are scaffolding, not content. "What did anyone
        # suggest" was filtering text on "anyone" and matching nothing, while
        # resolving the act perfectly - which is the exact failure the
        # retrieval eval exists to catch.
        "anyone", "anybody", "everyone", "everybody", "somebody", "nobody",
        "else", "thing", "things", "stuff",
        "ago", "last", "next", "day", "days", "week", "weeks", "month",
        "months", "time", "times", "happened", "happen", "going", "go",
        "get", "got", "would", "will", "can", "could", "should", "have",
        "has", "had", "not", "no", "yes",
    }
)


class QuestionParser:
    def __init__(
        self,
        *,
        people: PersonService,
        utterances: UtteranceService,
    ) -> None:
        self._people = people
        self._utterances = utterances

    # --- parsing ----------------------------------------------------------

    async def parse(
        self,
        text: str,
        *,
        user_id: str,
        tz: str = "UTC",
        now: dt.datetime | None = None,
        anchor_ranges: list[TimeRange] | None = None,
    ) -> ResolvedQuestion:
        """Turn a question into a filter, or into a request to clarify."""
        resolved = ResolvedQuestion(text=text.strip())
        lowered = normalise(text)
        if not lowered:
            return resolved

        anchor = now or self._local_now(tz)

        consumed: set[str] = set()
        self._resolve_acts(resolved, lowered, consumed)
        await self._resolve_people(resolved, text, user_id, consumed)
        self._resolve_time(resolved, text, anchor, anchor_ranges, consumed)
        await self._resolve_place(resolved, lowered, user_id, consumed)
        # Consume the question scaffolding before terms are collected, or
        # "how many times did I promise" leaves "many" behind and the filter
        # starts matching text on it.
        for phrase in (*AGGREGATION_WORDS, *EXISTENCE_WORDS):
            if phrase in lowered:
                consumed.update(phrase.split())

        self._resolve_terms(resolved, lowered, consumed)
        resolved.kind = self._classify(resolved, lowered)

        log_event(
            logger,
            "question.parsed",
            user_id=user_id,
            kind=resolved.kind.value,
            # The filter, never the question text: a question can contain
            # anything the user said out loud.
            filter=resolved.describe(),
            clarifications=[c.kind.value for c in resolved.clarifications],
        )
        return resolved

    @staticmethod
    def _local_now(tz: str) -> dt.datetime:
        try:
            return utc_now().astimezone(ZoneInfo(tz))
        except Exception:
            return utc_now()

    # --- the individual resolvers ----------------------------------------

    @staticmethod
    def _resolve_acts(
        resolved: ResolvedQuestion, lowered: str, consumed: set[str]
    ) -> None:
        words = set(lowered.split())
        acts: list[SpeechAct] = []
        for word, act in ACT_WORDS.items():
            if word in words and act not in acts:
                acts.append(act)
                consumed.add(word)
        resolved.acts = acts

        if words & OWING_WORDS:
            # "What do I owe" spans commitment, assignment and requirement,
            # so it is a flag rather than a single act.
            resolved.actionable_only = True
            consumed |= words & OWING_WORDS

    async def _resolve_people(
        self,
        resolved: ResolvedQuestion,
        text: str,
        user_id: str,
        consumed: set[str],
    ) -> None:
        """Match names against known people, and ask when a name is unclear.

        Only the automatic tier of `PersonService` resolves. A name that
        merely *resembles* two people becomes a clarification, because
        picking one would attribute speech to whoever sorted first.
        """
        people = await self._people.list_people(user_id)
        words = normalise(text).split()

        if set(words) & SELF_WORDS:
            resolved.about_self = True
            consumed |= set(words) & SELF_WORDS

        # Try the longest spans first, so "Pulkit Verma" wins over "Pulkit".
        for size in (3, 2, 1):
            for index in range(len(words) - size + 1):
                span = words[index : index + size]
                phrase = " ".join(span)
                if phrase in consumed or phrase in STOPWORDS:
                    continue
                # A name contains no question scaffolding, and skipping such
                # spans is what stops "what did pulkit" matching a person
                # called Pulkit: `Person.matches` treats token containment as
                # a match in either direction, so any phrase *containing* the
                # name would otherwise resolve - and the clarification then
                # reads "Which what did pulkit do you mean?".
                if any(word in STOPWORDS for word in span):
                    continue
                matched = [
                    person for person in people if person.matches(phrase)
                ]
                if len(matched) == 1:
                    person = matched[0]
                    if person.id not in resolved.person_ids:
                        resolved.person_ids.append(person.id)
                        resolved.person_names.append(person.canonical_name)
                        if person.is_self:
                            resolved.about_self = True
                    consumed.update(words[index : index + size])
                elif len(matched) > 1:
                    resolved.clarifications.append(
                        Clarification(
                            kind=ClarificationKind.PERSON,
                            phrase=phrase,
                            question=f"Which {phrase} do you mean?",
                            options=[
                                ClarificationOption(
                                    value=person.id,
                                    label=person.canonical_name,
                                    detail=(
                                        f"{person.mention_count} mentions"
                                    ),
                                )
                                for person in matched
                            ],
                        )
                    )
                    consumed.update(words[index : index + size])

    @staticmethod
    def _resolve_time(
        resolved: ResolvedQuestion,
        text: str,
        anchor: dt.datetime,
        anchor_ranges: list[TimeRange] | None,
        consumed: set[str],
    ) -> None:
        when = resolve_when(text, now=anchor, anchor_ranges=anchor_ranges)
        if when.phrase:
            consumed.update(normalise(when.phrase).split())

        if when.ambiguous:
            resolved.time_phrase = when.phrase
            resolved.clarifications.append(
                Clarification(
                    kind=ClarificationKind.TIME,
                    phrase=when.phrase or "",
                    question=(
                        f"Which day do you mean by {when.phrase!r}?"
                        if when.phrase
                        else "Which day do you mean?"
                    ),
                    options=[
                        ClarificationOption(
                            value=window.start.isoformat(), label=window.label
                        )
                        for window in when.ranges
                    ],
                )
            )
            return

        window = when.resolved
        if window is not None:
            resolved.time_range = ResolvedTimeRange(
                start=window.start, end=window.end, label=window.label
            )
            resolved.time_phrase = when.phrase

    async def _resolve_place(
        self,
        resolved: ResolvedQuestion,
        lowered: str,
        user_id: str,
        consumed: set[str],
    ) -> None:
        """Match against places that actually appear in the index.

        Matching against a geocoder's vocabulary would accept "at the Taj
        Mahal" and then return nothing, which looks like a retrieval failure
        rather than a place the user has never recorded at.
        """
        rows, _ = await self._utterances.query(user_id=user_id, limit=2000)
        names = {
            row.place.describe()
            for row in rows
            if row.place is not None and row.place.describe()
        }
        for name in sorted(names, key=len, reverse=True):
            needle = normalise(name)
            if needle and needle in lowered:
                resolved.place = name
                consumed.update(needle.split())
                return

    @staticmethod
    def _resolve_terms(
        resolved: ResolvedQuestion, lowered: str, consumed: set[str]
    ) -> None:
        resolved.terms = [
            word
            for word in lowered.split()
            if word not in consumed
            and word not in STOPWORDS
            and len(word) > 2
        ]

    @staticmethod
    def _classify(resolved: ResolvedQuestion, lowered: str) -> QuestionKind:
        for phrase in EXISTENCE_WORDS:
            if phrase in lowered:
                return QuestionKind.EXISTENCE
        for phrase in AGGREGATION_WORDS:
            if phrase in lowered:
                return QuestionKind.AGGREGATION
        if resolved.person_ids or (
            resolved.about_self and resolved.actionable_only
        ):
            return QuestionKind.RELATIONSHIP
        if resolved.time_range is not None:
            return QuestionKind.TEMPORAL
        if resolved.acts or resolved.terms or resolved.place:
            return QuestionKind.POINT_LOOKUP
        return QuestionKind.UNKNOWN

    # --- answering --------------------------------------------------------

    async def answer(
        self,
        text: str,
        *,
        user_id: str,
        tz: str = "UTC",
        now: dt.datetime | None = None,
        anchor_ranges: list[TimeRange] | None = None,
        limit: int = 20,
    ) -> QuestionAnswer:
        """Resolve, then run the filter - or refuse, and say why.

        Three refusals, each one a case where returning rows would be worse
        than returning none:

        - **needs clarification** - a guessed constraint returns real
          sentences from the wrong person or moment, which reads as an answer.
        - **not understood** - better than silently matching on stopwords.
        - **no constraints** - an unconstrained filter returns the user's
          entire history dressed as a reply.
        """
        resolved = await self.parse(
            text, user_id=user_id, tz=tz, now=now, anchor_ranges=anchor_ranges
        )

        if resolved.needs_clarification:
            return QuestionAnswer(
                resolved=resolved,
                verdict=resolved.clarifications[0].question,
            )
        if resolved.kind is QuestionKind.UNKNOWN:
            return QuestionAnswer(
                resolved=resolved,
                verdict=(
                    "I could not turn that into a search. Try naming a "
                    "person, a day, a place, or what was decided."
                ),
            )
        if resolved.is_empty:
            return QuestionAnswer(
                resolved=resolved,
                verdict="That would search everything you have ever recorded.",
            )

        act = resolved.acts[0] if len(resolved.acts) == 1 else None
        rows, total = await self._utterances.query(
            user_id=user_id,
            since=resolved.time_range.start if resolved.time_range else None,
            until=resolved.time_range.end if resolved.time_range else None,
            act=act,
            actionable_only=resolved.actionable_only,
            place=resolved.place,
            contains=resolved.terms[0] if resolved.terms else None,
            limit=limit,
        )

        # More than one act was named: filter the rest in memory rather than
        # running the query repeatedly.
        if len(resolved.acts) > 1:
            wanted = set(resolved.acts)
            rows = [row for row in rows if row.act in wanted]
            total = len(rows)

        if not rows:
            return QuestionAnswer(
                resolved=resolved,
                total=0,
                verdict=(
                    f"Nothing recorded matches {resolved.describe()}."
                ),
            )

        return QuestionAnswer(
            resolved=resolved,
            total=total,
            utterances=[
                UtteranceResponse.from_domain(row).model_dump(mode="json")
                for row in rows
            ],
            verdict=f"{total} matched {resolved.describe()}.",
        )
