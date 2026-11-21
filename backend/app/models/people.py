"""People: canonical identities for the names in conversations.

## Why this is phase B, before the index and before embeddings

`owner` has been a free string since phase 3, and that was fine until the
first real recording, where Whisper heard "Pulkit" as **"Pull kit"**. Those
are two people as far as every query is concerned, and the product's whole
claim is *who committed to what*. A graph whose nodes are strings is not a
graph.

Everything downstream in [MEMORY_ARCHITECTURE.md] depends on this: an
utterance index needs a speaker id, a bi-temporal edge needs endpoints, and
"what has Priya committed to?" needs Priya to be one thing.

## The resolution policy, which is the whole design

Two tiers, and the gap between them is deliberate.

**Automatic** - only when identity is near-certain:
  * the normalised name equals the canonical name or any stored alias
  * the *squashed* forms match - spacing removed and repeated letters
    collapsed, so "Pull kit" resolves onto "Pulkit"
  * one name's tokens contain the other's ("manan" vs "manan dudeja")

**Suggested, never applied** - everything merely similar. "Pulkit" and
"Pulkit Sharma" might be the same person; "Priya" and "Prithvi" are not, and
no similarity score can tell those cases apart. So the system *proposes* and
the user decides, exactly as it does for approvals: a wrong merge silently
reassigns someone's commitments and is close to impossible to notice
afterwards, while an unmerged duplicate is visible and one tap to fix.

The squashing rule earns its place specifically because ASR errors of that
shape - wrong word boundary, doubled letter - are a large, well-behaved class
where the phonemes were already right. See `squash`, whose scope was widened
after a test showed the original despacing rule did not actually fix the bug
it was written for.

Token containment is automatic too, which is a deliberate personal-scale
assumption: in one person's conversations, "Pulkit" and "Pulkit Verma" are
the same man far more often than they are two men. If that ever stops being
true, it belongs in the suggestion tier, not in a threshold.

## `is_self`, and why it beats matching the display name

The open-loops ledger currently decides "do I owe this?" by comparing the
owner string against the account's `display_name`. That fails on a nickname,
on a surname, and on every recording where the speaker is never named.

One tap - "this is me" - fixes attribution permanently, for every past and
future task, and it is information only the user has. `OWNER_UNKNOWN` and
`OWNER_GROUP` are never people: they are sentinels, and creating a "Person"
called "unknown" would quietly turn an absence into an identity.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.core.text import normalise, quote_similarity
from app.models.analysis import OWNER_GROUP, OWNER_UNKNOWN
from app.models.domain import StoredModel

#: Similarity at or above which two names are offered as a possible merge.
#: Only ever a suggestion - see the module docstring.
SUGGEST_THRESHOLD = 0.72

#: Names we refuse to treat as people, whatever they look like.
NON_NAMES = frozenset(
    {
        OWNER_UNKNOWN,
        OWNER_GROUP,
        "",
        "none",
        "null",
        "n/a",
        "someone",
        "somebody",
        "everyone",
        "anyone",
        "they",
        "he",
        "she",
        "it",
        "we",
        "us",
        "i",
        "me",
        "you",
    }
)


def despace(value: str) -> str:
    """Normalised with all spacing removed."""
    return normalise(value).replace(" ", "")


def squash(value: str) -> str:
    """Despaced, with runs of the same letter collapsed to one.

    This is the actual ASR fix, and the shape of it was a correction.
    Despacing alone was written to resolve the real failure - Whisper hearing
    "Pulkit" as "Pull kit" - and it does not, because the mis-hearing also
    **doubled the L**: `despace("Pull kit")` is "pullkit", not "pulkit". A
    test caught that; the assumption had been wrong.

    Collapsing repeated letters as well handles it, and gemination errors
    ("Ravi"/"Raavi", "Pulkit"/"Pullkit") are a large, well-behaved class in
    transcribed Indian names specifically. It stays in the automatic tier
    because two names that differ *only* by letter doubling and spacing being
    different people is vanishingly rare - unlike general edit distance,
    where "Priya" and "Prithvi" are close and are not the same person.
    """
    despaced = despace(value)
    out: list[str] = []
    for character in despaced:
        if not out or out[-1] != character:
            out.append(character)
    return "".join(out)


def is_name(value: str) -> bool:
    """Whether this string could denote a person at all.

    Pronouns are excluded here rather than resolved: resolving "he" needs the
    conversation, which only the extractor has (prompt rule 14). By the time
    a string reaches this layer, an unresolved pronoun is an absence.
    """
    cleaned = normalise(value)
    if not cleaned or cleaned in NON_NAMES:
        return False
    # A bare number, or punctuation that survived normalising, is not a name.
    return any(character.isalpha() for character in cleaned)


class Person(StoredModel):
    """One human, with every string we have ever seen them called."""

    user_id: str
    canonical_name: str

    #: Every other form seen or added, stored normalised. The canonical name
    #: is not duplicated here.
    aliases: list[str] = Field(default_factory=list)

    #: True for the account holder. Set by the user, never inferred: guessing
    #: would mean deciding whose commitments these are on a name match.
    is_self: bool = False

    #: Links to an enrolled voiceprint, when there is one. Only ever set for
    #: `is_self`, because only the owner is enrolled - see `models/voice.py`.
    voice_profile_id: str | None = None

    # --- provenance ---
    source_session_ids: list[str] = Field(default_factory=list)
    mention_count: int = 0
    first_seen: dt.datetime | None = None
    last_seen: dt.datetime | None = None

    #: Ids absorbed by a merge. Kept so that a merged-away id still resolves
    #: and old references do not dangle.
    merged_from: list[str] = Field(default_factory=list)

    notes: str = ""

    @field_validator("canonical_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("canonical_name must not be blank")
        return cleaned

    @property
    def keys(self) -> set[str]:
        """Every normalised string that should resolve to this person."""
        forms = {normalise(self.canonical_name), *self.aliases}
        return {form for form in forms if form}

    @property
    def squashed_keys(self) -> set[str]:
        return {squash(form) for form in self.keys if form}

    def matches(self, name: str) -> bool:
        """Automatic-tier match only. Deliberately strict."""
        candidate = normalise(name)
        if not candidate:
            return False
        if candidate in self.keys:
            return True
        if squash(candidate) in self.squashed_keys:
            return True
        # "manan" vs "manan dudeja": one name's tokens contain the other's.
        candidate_tokens = set(candidate.split())
        if not candidate_tokens:
            return False
        for form in self.keys:
            form_tokens = set(form.split())
            if not form_tokens:
                continue
            if candidate_tokens <= form_tokens or form_tokens <= candidate_tokens:
                return True
        return False

    def similarity(self, name: str) -> float:
        """How alike, for suggestion purposes only."""
        candidate = normalise(name)
        if not candidate:
            return 0.0
        return max(
            (quote_similarity(candidate, form) for form in self.keys),
            default=0.0,
        )


class MergeSuggestion(BaseModel):
    """Two people who might be one. Offered to the user, never applied.

    Carries `shared_sessions` because appearing in the same conversation is
    weak evidence *against* a merge - two names used in one exchange are
    usually two people - and the user deserves to see that rather than a bare
    score.
    """

    primary_id: str
    primary_name: str
    other_id: str
    other_name: str
    similarity: float
    shared_sessions: int = 0
    reason: str = ""


class PersonSummary(BaseModel):
    """What the app lists. No internal keys."""

    id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    is_self: bool = False
    mention_count: int = 0
    session_count: int = 0
    first_seen: dt.datetime | None = None
    last_seen: dt.datetime | None = None
    has_voiceprint: bool = False

    @classmethod
    def from_domain(cls, person: Person) -> PersonSummary:
        return cls(
            id=person.id,
            canonical_name=person.canonical_name,
            aliases=sorted(person.aliases),
            is_self=person.is_self,
            mention_count=person.mention_count,
            session_count=len(set(person.source_session_ids)),
            first_seen=person.first_seen,
            last_seen=person.last_seen,
            has_voiceprint=person.voice_profile_id is not None,
        )


class PeopleList(BaseModel):
    people: list[PersonSummary] = Field(default_factory=list)
    suggestions: list[MergeSuggestion] = Field(default_factory=list)
