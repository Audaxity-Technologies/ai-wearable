"""Tests for person identity.

The risk is not failing to merge. It is **merging two different people**,
which silently reassigns someone's commitments and is close to impossible to
notice afterwards. Most of these tests are about the resolver refusing.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.analysis import OWNER_GROUP, OWNER_UNKNOWN
from app.models.people import Person, despace, is_name, squash
from app.repositories.json_repo import JsonRepository
from app.services.people import PersonService

WHEN = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def service(tmp_path) -> PersonService:
    return PersonService(
        people=JsonRepository(tmp_path / "people.json", Person)
    )


# --- what is and is not a name ------------------------------------------


@pytest.mark.parametrize("value", ["Pulkit", "Priya Sharma", "Ravi"])
def test_real_names_are_names(value: str) -> None:
    assert is_name(value)


@pytest.mark.parametrize(
    "value",
    [OWNER_UNKNOWN, OWNER_GROUP, "", "   ", "someone", "they", "he", "we", "123", "-"],
)
def test_sentinels_and_pronouns_are_not_names(value: str) -> None:
    """A Person called "unknown" would turn an absence into an identity."""
    assert not is_name(value)


async def test_sentinels_never_create_a_person(service: PersonService) -> None:
    for value in (OWNER_UNKNOWN, OWNER_GROUP, "", "someone", "he"):
        assert await service.resolve_or_create("u1", value) is None
    assert await service.list_people("u1") == []


# --- the ASR fix this phase exists for ----------------------------------


def test_despacing_alone_does_not_fix_the_real_bug() -> None:
    """Documents the correction, so nobody re-narrows `squash` later.

    Despacing was written to resolve "Pulkit" heard as "Pull kit" and does
    not, because the mis-hearing also doubled the L.
    """
    assert despace("Pull kit") == "pullkit"
    assert despace("Pull kit") != despace("Pulkit")


def test_squashing_collapses_spacing_and_doubled_letters() -> None:
    assert squash("Pull kit") == squash("Pulkit") == "pulkit"
    # Gemination in either direction.
    assert squash("Raavi") == squash("Ravi")


def test_squashing_does_not_collapse_genuinely_different_names() -> None:
    assert squash("Priya") != squash("Prithvi")


async def test_whisper_splitting_a_name_resolves_to_one_person(
    service: PersonService,
) -> None:
    """The actual bug: the first real recording heard "Pulkit" as "Pull kit".

    Two people as far as every query was concerned, in a product whose whole
    claim is who committed to what.
    """
    first = await service.resolve_or_create("u1", "Pulkit", session_id="s1")
    second = await service.resolve_or_create("u1", "Pull kit", session_id="s2")
    assert first is not None and second is not None
    assert first.id == second.id
    assert len(await service.list_people("u1")) == 1


async def test_resolving_a_variant_learns_it_as_an_alias(
    service: PersonService,
) -> None:
    """So the next occurrence matches by exact key, not by the despacing rule."""
    await service.resolve_or_create("u1", "Pulkit", session_id="s1")
    person = await service.resolve_or_create("u1", "Pull kit", session_id="s2")
    assert person is not None
    assert "pull kit" in person.aliases


async def test_a_first_name_matches_a_full_name(service: PersonService) -> None:
    first = await service.resolve_or_create("u1", "Manan Dudeja")
    second = await service.resolve_or_create("u1", "Manan")
    assert first is not None and second is not None
    assert first.id == second.id


async def test_matching_ignores_case_and_punctuation(
    service: PersonService,
) -> None:
    first = await service.resolve_or_create("u1", "Priya")
    second = await service.resolve_or_create("u1", "  PRIYA.  ")
    assert first is not None and second is not None
    assert first.id == second.id


# --- the refusals --------------------------------------------------------


async def test_merely_similar_names_stay_separate(
    service: PersonService,
) -> None:
    """"Priya" and "Prithvi" are close, and are not the same person.

    No similarity score can tell this case apart from "Pulkit"/"Pulkit
    Sharma", which is exactly why similarity never merges automatically.
    """
    priya = await service.resolve_or_create("u1", "Priya")
    prithvi = await service.resolve_or_create("u1", "Prithvi")
    assert priya is not None and prithvi is not None
    assert priya.id != prithvi.id
    assert len(await service.list_people("u1")) == 2


async def test_similar_names_are_offered_as_a_suggestion_only(
    service: PersonService,
) -> None:
    await service.resolve_or_create("u1", "Priya", session_id="s1")
    await service.resolve_or_create("u1", "Priyanka", session_id="s2")

    people = await service.list_people("u1")
    suggestions = await service.suggestions("u1")
    # Still two people: the suggestion changed nothing on its own.
    assert len(people) == 2
    assert len(suggestions) >= 1
    assert suggestions[0].similarity >= 0.72


async def test_a_suggestion_says_when_the_pair_appear_together(
    service: PersonService,
) -> None:
    """Appearing in one conversation is evidence AGAINST a merge.

    Two names used in the same exchange are usually two people, and the user
    deserves to see that rather than a bare score.
    """
    await service.resolve_or_create("u1", "Priya", session_id="s1")
    await service.resolve_or_create("u1", "Priyanka", session_id="s1")
    suggestion = (await service.suggestions("u1"))[0]
    assert suggestion.shared_sessions == 1
    assert "two different people" in suggestion.reason


async def test_already_matching_people_are_not_suggested(
    service: PersonService,
) -> None:
    """They are already one person; a suggestion would be noise."""
    await service.resolve_or_create("u1", "Pulkit")
    await service.resolve_or_create("u1", "Pull kit")
    assert await service.suggestions("u1") == []


async def test_people_are_scoped_per_account(service: PersonService) -> None:
    mine = await service.resolve_or_create("u1", "Pulkit")
    theirs = await service.resolve_or_create("u2", "Pulkit")
    assert mine is not None and theirs is not None
    assert mine.id != theirs.id
    assert len(await service.list_people("u1")) == 1


# --- aliases -------------------------------------------------------------


async def test_an_alias_claimed_by_someone_else_is_refused(
    service: PersonService,
) -> None:
    """Two people answering to one name makes the resolver order-dependent,
    which is how attribution silently becomes a coin flip."""
    priya = await service.resolve_or_create("u1", "Priya")
    await service.resolve_or_create("u1", "Prithvi")
    assert priya is not None

    with pytest.raises(ConflictError, match="Merge them instead"):
        await service.add_alias(priya.id, "Prithvi")


async def test_an_alias_can_be_added_and_then_resolves(
    service: PersonService,
) -> None:
    person = await service.resolve_or_create("u1", "Pulkit")
    assert person is not None
    await service.add_alias(person.id, "PK")
    found = await service.find("u1", "pk")
    assert found is not None and found.id == person.id


async def test_a_pronoun_cannot_be_made_an_alias(
    service: PersonService,
) -> None:
    person = await service.resolve_or_create("u1", "Pulkit")
    assert person is not None
    with pytest.raises(ValidationError):
        await service.add_alias(person.id, "he")


async def test_renaming_keeps_the_old_name_as_an_alias(
    service: PersonService,
) -> None:
    """The old spelling is what appears in the stored quotes and transcripts,
    so dropping it would stop those resolving."""
    person = await service.resolve_or_create("u1", "Pull kit")
    assert person is not None
    renamed = await service.rename(person.id, "Pulkit Verma")
    assert renamed.canonical_name == "Pulkit Verma"
    assert (await service.find("u1", "Pull kit")) is not None


# --- is_self -------------------------------------------------------------


async def test_is_self_is_never_inferred(service: PersonService) -> None:
    person = await service.resolve_or_create("u1", "Manan")
    assert person is not None
    assert person.is_self is False
    assert await service.self_person("u1") is None


async def test_only_one_person_can_be_self(service: PersonService) -> None:
    """Two "me"s would make the ledger's own/owed split meaningless."""
    first = await service.resolve_or_create("u1", "Manan")
    second = await service.resolve_or_create("u1", "Pulkit")
    assert first is not None and second is not None

    await service.set_self(first.id, True)
    await service.set_self(second.id, True)

    people = await service.list_people("u1")
    selves = [person for person in people if person.is_self]
    assert len(selves) == 1
    assert selves[0].id == second.id


async def test_self_names_come_from_the_marked_person(
    service: PersonService,
) -> None:
    person = await service.resolve_or_create("u1", "Manan Dudeja")
    assert person is not None
    await service.add_alias(person.id, "MD")
    await service.set_self(person.id, True)

    names = await service.self_names("u1")
    assert "Manan Dudeja" in names
    assert "md" in names


async def test_self_names_is_empty_when_nobody_is_marked(
    service: PersonService,
) -> None:
    await service.resolve_or_create("u1", "Manan")
    assert await service.self_names("u1") == []


# --- merging -------------------------------------------------------------


async def test_merge_keeps_every_name_and_every_session(
    service: PersonService,
) -> None:
    primary = await service.resolve_or_create("u1", "Priya", session_id="s1")
    other = await service.resolve_or_create("u1", "Priyanka", session_id="s2")
    assert primary is not None and other is not None

    merged = await service.merge(primary.id, other.id)
    assert set(merged.source_session_ids) == {"s1", "s2"}
    assert merged.mention_count == 2
    assert (await service.find("u1", "Priyanka")) is not None
    assert (await service.find("u1", "Priya")) is not None
    assert len(await service.list_people("u1")) == 1


async def test_a_merged_away_id_still_resolves(service: PersonService) -> None:
    """References held elsewhere - tasks, for one - must not dangle."""
    primary = await service.resolve_or_create("u1", "Priya")
    other = await service.resolve_or_create("u1", "Priyanka")
    assert primary is not None and other is not None
    absorbed_id = other.id

    await service.merge(primary.id, other.id)
    followed = await service.resolve_id(absorbed_id)
    assert followed is not None and followed.id == primary.id


async def test_merging_preserves_is_self_in_either_direction(
    service: PersonService,
) -> None:
    """Merging the account holder into someone else must not quietly stop the
    ledger knowing who the user is."""
    me = await service.resolve_or_create("u1", "Manan")
    variant = await service.resolve_or_create("u1", "Manon")
    assert me is not None and variant is not None
    await service.set_self(me.id, True)

    merged = await service.merge(variant.id, me.id)
    assert merged.is_self is True


async def test_merging_a_person_into_themselves_is_refused(
    service: PersonService,
) -> None:
    person = await service.resolve_or_create("u1", "Pulkit")
    assert person is not None
    with pytest.raises(ValidationError):
        await service.merge(person.id, person.id)


async def test_merging_across_accounts_is_refused(
    service: PersonService,
) -> None:
    mine = await service.resolve_or_create("u1", "Pulkit")
    theirs = await service.resolve_or_create("u2", "Pulkit")
    assert mine is not None and theirs is not None
    with pytest.raises((ValidationError, NotFoundError)):
        await service.merge(mine.id, theirs.id, user_id="u1")


async def test_merge_takes_the_earliest_and_latest_sightings(
    service: PersonService,
) -> None:
    early = await service.resolve_or_create(
        "u1", "Priya", at=WHEN - dt.timedelta(days=5)
    )
    late = await service.resolve_or_create(
        "u1", "Priyanka", at=WHEN + dt.timedelta(days=5)
    )
    assert early is not None and late is not None

    merged = await service.merge(early.id, late.id)
    assert merged.first_seen == WHEN - dt.timedelta(days=5)
    assert merged.last_seen == WHEN + dt.timedelta(days=5)


# --- ownership and deletion ---------------------------------------------


async def test_another_accounts_person_is_reported_as_missing(
    service: PersonService,
) -> None:
    theirs = await service.resolve_or_create("u2", "Pulkit")
    assert theirs is not None
    with pytest.raises(NotFoundError):
        await service.get(theirs.id, user_id="u1")


async def test_deleting_a_person_leaves_the_rest_intact(
    service: PersonService,
) -> None:
    person = await service.resolve_or_create("u1", "Pulkit")
    other = await service.resolve_or_create("u1", "Priya")
    assert person is not None and other is not None

    await service.delete(person.id, user_id="u1")
    remaining = await service.list_people("u1")
    assert [p.id for p in remaining] == [other.id]


# --- ordering ------------------------------------------------------------


async def test_the_user_is_listed_first_then_the_most_mentioned(
    service: PersonService,
) -> None:
    me = await service.resolve_or_create("u1", "Manan")
    assert me is not None
    await service.set_self(me.id, True)

    for _ in range(5):
        await service.resolve_or_create("u1", "Pulkit")
    await service.resolve_or_create("u1", "Priya")

    names = [person.canonical_name for person in await service.list_people("u1")]
    assert names[0] == "Manan"
    assert names[1] == "Pulkit"


async def test_token_containment_is_automatic_by_design(
    service: PersonService,
) -> None:
    """A deliberate personal-scale assumption, asserted so it is not an
    accident: in one person's conversations "Pulkit" and "Pulkit Verma" are
    the same man far more often than they are two men.

    If that ever stops being true it belongs in the suggestion tier, and this
    test is what should fail first.
    """
    first = await service.resolve_or_create("u1", "Pulkit")
    second = await service.resolve_or_create("u1", "Pulkit Verma")
    assert first is not None and second is not None
    assert first.id == second.id
