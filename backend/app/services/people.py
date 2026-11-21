"""Resolving names to people, and merging duplicates.

See `app/models/people.py` for the two-tier policy and why the gap between
automatic and suggested is deliberate.
"""

from __future__ import annotations

import datetime as dt

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.text import normalise
from app.core.time_utils import utc_now
from app.models.people import (
    SUGGEST_THRESHOLD,
    MergeSuggestion,
    Person,
    is_name,
    squash,
)
from app.repositories.base import Repository

logger = get_logger(__name__)


class PersonService:
    def __init__(self, *, people: Repository[Person]) -> None:
        self._people = people

    # --- resolution ------------------------------------------------------

    async def find(self, user_id: str, name: str) -> Person | None:
        """The person this name denotes, by automatic-tier rules only.

        Returns `None` rather than a best guess. Every caller's fallback is
        already "we do not know who", which is a correct answer throughout
        this codebase.
        """
        if not is_name(name):
            return None
        for person in await self._people.list(user_id=user_id):
            if person.matches(name):
                return person
        return None

    async def resolve_or_create(
        self,
        user_id: str,
        name: str,
        *,
        session_id: str | None = None,
        at: dt.datetime | None = None,
    ) -> Person | None:
        """Find this person, or record a new one.

        `None` for sentinels, pronouns and anything that is not a name - so
        `OWNER_UNKNOWN` never becomes a person called "unknown", which would
        turn an absence into an identity.

        A new alias is learned here too: resolving "Pull kit" onto Pulkit adds
        that spelling, so the next occurrence matches by exact key rather than
        by the despacing rule.
        """
        if not is_name(name):
            return None

        when = at or utc_now()
        person = await self.find(user_id, name)
        if person is None:
            person = Person(
                user_id=user_id,
                canonical_name=name.strip(),
                mention_count=1,
                first_seen=when,
                last_seen=when,
                source_session_ids=[session_id] if session_id else [],
            )
            await self._people.put(person)
            log_event(
                logger,
                "people.created",
                user_id=user_id,
                person_id=person.id,
                session_id=session_id,
            )
            return person

        changed = False
        normalised = normalise(name)
        if normalised and normalised not in person.keys:
            # Learn the variant we just resolved, so the next match is exact.
            person.aliases.append(normalised)
            changed = True
        person.mention_count += 1
        if session_id and session_id not in person.source_session_ids:
            person.source_session_ids.append(session_id)
        if person.first_seen is None or when < person.first_seen:
            person.first_seen = when
        if person.last_seen is None or when > person.last_seen:
            person.last_seen = when
        person.touch()
        await self._people.put(person)
        if changed:
            log_event(
                logger,
                "people.alias_learned",
                user_id=user_id,
                person_id=person.id,
            )
        return person

    # --- reads -----------------------------------------------------------

    async def get(self, person_id: str, *, user_id: str | None = None) -> Person:
        person = await self._people.get(person_id)
        if person is None or (user_id and person.user_id != user_id):
            # Reported as missing rather than forbidden, as everywhere else.
            raise NotFoundError(f"Person not found: {person_id}")
        return person

    async def list_people(self, user_id: str) -> list[Person]:
        people = await self._people.list(user_id=user_id)
        # The user first, then by how often they come up: the long tail of
        # one-off mentions is rarely what someone opened this screen for.
        return sorted(
            people,
            key=lambda person: (
                not person.is_self,
                -person.mention_count,
                normalise(person.canonical_name),
            ),
        )

    async def self_person(self, user_id: str) -> Person | None:
        for person in await self._people.list(user_id=user_id):
            if person.is_self:
                return person
        return None

    async def suggestions(self, user_id: str) -> list[MergeSuggestion]:
        """Pairs that might be one person. Never applied automatically.

        Pairs that already match automatically are excluded - they are
        already one person - and so is any pair where either side is the
        other's alias.
        """
        people = await self.list_people(user_id)
        out: list[MergeSuggestion] = []

        for index, primary in enumerate(people):
            for other in people[index + 1 :]:
                if primary.matches(other.canonical_name):
                    continue  # already the same person by the strict rules
                score = primary.similarity(other.canonical_name)
                if score < SUGGEST_THRESHOLD:
                    continue
                shared = len(
                    set(primary.source_session_ids)
                    & set(other.source_session_ids)
                )
                out.append(
                    MergeSuggestion(
                        primary_id=primary.id,
                        primary_name=primary.canonical_name,
                        other_id=other.id,
                        other_name=other.canonical_name,
                        similarity=round(score, 3),
                        shared_sessions=shared,
                        reason=(
                            "Similar names, but they appear in the same "
                            f"{shared} conversation(s) - which usually means "
                            "two different people."
                            if shared
                            else "Similar names, never mentioned together."
                        ),
                    )
                )

        return sorted(out, key=lambda entry: entry.similarity, reverse=True)

    # --- edits -----------------------------------------------------------

    async def add_alias(
        self, person_id: str, alias: str, *, user_id: str | None = None
    ) -> Person:
        person = await self.get(person_id, user_id=user_id)
        if not is_name(alias):
            raise ValidationError(f"{alias!r} cannot be used as a name.")

        normalised = normalise(alias)
        owner = await self.find(person.user_id, alias)
        if owner is not None and owner.id != person.id:
            # Two people cannot answer to one name; the resolver would then
            # depend on iteration order, which is how attribution silently
            # becomes a coin flip.
            raise ConflictError(
                f"{alias!r} already belongs to {owner.canonical_name}. "
                "Merge them instead."
            )

        if normalised not in person.keys:
            person.aliases.append(normalised)
            person.touch()
            await self._people.put(person)
        return person

    async def remove_alias(
        self, person_id: str, alias: str, *, user_id: str | None = None
    ) -> Person:
        person = await self.get(person_id, user_id=user_id)
        normalised = normalise(alias)
        if normalised in person.aliases:
            person.aliases.remove(normalised)
            person.touch()
            await self._people.put(person)
        return person

    async def rename(
        self, person_id: str, name: str, *, user_id: str | None = None
    ) -> Person:
        """Change the display name, keeping the old one as an alias.

        Keeping it matters: the old spelling is what appears in the stored
        quotes and transcripts, so dropping it would stop those resolving.
        """
        person = await self.get(person_id, user_id=user_id)
        if not is_name(name):
            raise ValidationError(f"{name!r} cannot be used as a name.")

        previous = normalise(person.canonical_name)
        person.canonical_name = name.strip()
        if previous and previous not in person.keys:
            person.aliases.append(previous)
        person.touch()
        await self._people.put(person)
        return person

    async def set_self(
        self, person_id: str, is_self: bool, *, user_id: str | None = None
    ) -> Person:
        """Mark (or unmark) this person as the account holder.

        Exactly one person can be `is_self`, so setting it clears any other.
        Two "me"s would make the ledger's own/owed split meaningless.
        """
        person = await self.get(person_id, user_id=user_id)
        if is_self:
            for other in await self._people.list(user_id=person.user_id):
                if other.is_self and other.id != person.id:
                    other.is_self = False
                    other.touch()
                    await self._people.put(other)
        person.is_self = is_self
        person.touch()
        await self._people.put(person)
        log_event(
            logger,
            "people.self_set",
            user_id=person.user_id,
            person_id=person.id,
            is_self=is_self,
        )
        return person

    async def merge(
        self, primary_id: str, other_id: str, *, user_id: str | None = None
    ) -> Person:
        """Fold `other` into `primary`. Nothing is discarded.

        The absorbed person's names become aliases and its sessions and
        mention count are added, so no provenance is lost. Its id is recorded
        in `merged_from`, so references held elsewhere still resolve rather
        than dangling.
        """
        if primary_id == other_id:
            raise ValidationError("Cannot merge a person into themselves.")

        primary = await self.get(primary_id, user_id=user_id)
        other = await self.get(other_id, user_id=user_id)
        if primary.user_id != other.user_id:
            raise ValidationError("Cannot merge people across accounts.")

        for form in other.keys:
            if form not in primary.keys:
                primary.aliases.append(form)
        for session_id in other.source_session_ids:
            if session_id not in primary.source_session_ids:
                primary.source_session_ids.append(session_id)
        primary.mention_count += other.mention_count
        primary.merged_from.append(other.id)
        primary.merged_from.extend(other.merged_from)
        # `is_self` is sticky: merging the account holder into someone else
        # must not quietly stop the ledger knowing who the user is.
        primary.is_self = primary.is_self or other.is_self
        primary.voice_profile_id = (
            primary.voice_profile_id or other.voice_profile_id
        )
        for field in ("first_seen", "last_seen"):
            mine = getattr(primary, field)
            theirs = getattr(other, field)
            if theirs is None:
                continue
            if mine is None:
                setattr(primary, field, theirs)
            elif field == "first_seen":
                setattr(primary, field, min(mine, theirs))
            else:
                setattr(primary, field, max(mine, theirs))
        if other.notes and other.notes not in primary.notes:
            primary.notes = "\n".join(filter(None, [primary.notes, other.notes]))

        primary.touch()
        await self._people.put(primary)
        await self._people.delete(other.id)

        log_event(
            logger,
            "people.merged",
            user_id=primary.user_id,
            primary_id=primary.id,
            absorbed_id=other.id,
        )
        return primary

    async def resolve_id(self, person_id: str) -> Person | None:
        """Follow a possibly-merged id to the person who absorbed it."""
        person = await self._people.get(person_id)
        if person is not None:
            return person
        for candidate in await self._people.list():
            if person_id in candidate.merged_from:
                return candidate
        return None

    async def delete(self, person_id: str, *, user_id: str | None = None) -> None:
        person = await self.get(person_id, user_id=user_id)
        await self._people.delete(person.id)
        log_event(
            logger,
            "people.deleted",
            user_id=person.user_id,
            person_id=person.id,
        )

    # --- name keys for the ledger ----------------------------------------

    async def self_names(self, user_id: str) -> list[str]:
        """Every string that should count as "the user" for attribution.

        Drawn from the person marked `is_self`, which is a decision the user
        made, rather than from a display name that may never be spoken.
        """
        person = await self.self_person(user_id)
        if person is None:
            return []
        return [person.canonical_name, *person.aliases]


__all__ = ["PersonService", "squash"]
