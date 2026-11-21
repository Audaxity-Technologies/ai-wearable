"""Tests for coarse place capture.

The risk here is not losing a location. It is **keeping more than we said we
would**, and inferring one we never had. Most of these tests are about those
two things.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.models.places import (
    MAX_USEFUL_ACCURACY_M,
    PLACE_PRECISION_DP,
    CoarsePlace,
    round_coordinate,
    summarise_places,
)

# Somewhere in south Delhi, at a precision we must not keep.
PRECISE_LAT = 28.52451937
PRECISE_LON = 77.18554812


# --- the retention promise ----------------------------------------------


def test_coordinates_are_rounded_on_construction() -> None:
    """The server rounds too, not only the client.

    The client rounds so precision never leaves the device. The server rounds
    so the promise does not depend on the client being correct - which is the
    one that actually holds.
    """
    place = CoarsePlace(lat=PRECISE_LAT, lon=PRECISE_LON)
    assert place.lat == round(PRECISE_LAT, PLACE_PRECISION_DP)
    assert place.lon == round(PRECISE_LON, PLACE_PRECISION_DP)
    assert place.lat != PRECISE_LAT


def test_the_grid_is_about_a_hundred_metres_not_a_metre() -> None:
    """3dp is ~110m. Raising it is a privacy decision, not a tuning knob."""
    assert PLACE_PRECISION_DP == 3
    # Two points 10m apart collapse to the same cell; that is the point.
    near = CoarsePlace(lat=28.524519, lon=77.185548)
    nearer = CoarsePlace(lat=28.524601, lon=77.185502)
    assert near.grid_key == nearer.grid_key


def test_a_rebuilt_place_cannot_smuggle_precision_back_in() -> None:
    """Round-tripping through the model must not restore detail."""
    once = CoarsePlace(lat=PRECISE_LAT, lon=PRECISE_LON)
    twice = CoarsePlace(**once.model_dump())
    assert twice.lat == once.lat
    assert twice.lon == once.lon


def test_round_coordinate_handles_negatives_and_zero() -> None:
    assert round_coordinate(-77.185548) == -77.186
    assert round_coordinate(0.0) == 0.0


@pytest.mark.parametrize(
    "lat,lon",
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_impossible_coordinates_are_refused(lat: float, lon: float) -> None:
    with pytest.raises(ValueError):
        CoarsePlace(lat=lat, lon=lon)


# --- refusing to record a place we do not have --------------------------


def test_nothing_at_all_produces_no_place() -> None:
    """`None` is one state. An empty place object would be a second."""
    assert CoarsePlace.build() is None


def test_half_a_coordinate_is_not_a_location() -> None:
    assert CoarsePlace.build(lat=PRECISE_LAT) is None
    assert CoarsePlace.build(lon=PRECISE_LON) is None


def test_half_a_coordinate_keeps_the_names_it_came_with() -> None:
    """Dropping the point must not discard a usable label."""
    place = CoarsePlace.build(lat=PRECISE_LAT, locality="Hauz Khas")
    assert place is not None
    assert not place.has_point
    assert place.locality == "Hauz Khas"


def test_a_uselessly_loose_fix_drops_the_point() -> None:
    """A 5km fix cannot tell one place from another.

    Storing it as though it could is worse than storing nothing, because
    everything downstream would treat it as a real cell.
    """
    place = CoarsePlace.build(
        lat=PRECISE_LAT,
        lon=PRECISE_LON,
        locality="New Delhi",
        reported_accuracy_m=MAX_USEFUL_ACCURACY_M + 1,
    )
    assert place is not None
    assert not place.has_point
    assert place.grid_key is None
    assert place.locality == "New Delhi"


def test_a_loose_fix_with_no_names_produces_no_place() -> None:
    assert (
        CoarsePlace.build(
            lat=PRECISE_LAT,
            lon=PRECISE_LON,
            reported_accuracy_m=50_000,
        )
        is None
    )


def test_a_usable_fix_is_kept_with_its_reported_accuracy() -> None:
    """A 20m fix and a 1500m fix are the same cell but not the same evidence."""
    place = CoarsePlace.build(
        lat=PRECISE_LAT, lon=PRECISE_LON, reported_accuracy_m=18.0
    )
    assert place is not None
    assert place.has_point
    assert place.reported_accuracy_m == 18.0


def test_blank_names_are_treated_as_absent() -> None:
    assert CoarsePlace.build(label="   ", locality="") is None


# --- how a place is shown -----------------------------------------------


def test_describe_prefers_the_most_specific_name() -> None:
    place = CoarsePlace(
        label="Blue Tokai", locality="Hauz Khas", region="Delhi", country="IN"
    )
    assert place.describe() == "Blue Tokai"


def test_describe_falls_back_through_the_names() -> None:
    assert CoarsePlace(country="IN").describe() == "IN"


def test_describe_never_returns_coordinates() -> None:
    """Coordinates are an index, not an answer.

    Showing "28.524, 77.186" where a name belongs is technically honest and
    practically useless.
    """
    place = CoarsePlace(lat=PRECISE_LAT, lon=PRECISE_LON)
    described = place.describe()
    assert described == "an unnamed place"
    assert "28.5" not in described


# --- session summary ----------------------------------------------------


def test_segments_in_one_cell_become_one_place() -> None:
    entries = [
        (CoarsePlace(lat=28.524519, lon=77.185548, label="Office"), 30.0),
        (CoarsePlace(lat=28.524601, lon=77.185502), 60.0),
    ]
    summary = summarise_places(entries)
    assert len(summary) == 1
    assert summary[0].seconds == 90.0
    assert summary[0].segment_count == 2
    # The label is the part a person can recognise, so merging keeps it.
    assert summary[0].name == "Office"


def test_walking_between_places_produces_two_entries() -> None:
    """A session is not at one place. This is why place lives on the segment."""
    entries = [
        (CoarsePlace(lat=28.524, lon=77.185, label="Office"), 120.0),
        (CoarsePlace(lat=28.600, lon=77.220, label="Cafe"), 600.0),
    ]
    summary = summarise_places(entries)
    assert [entry.name for entry in summary] == ["Cafe", "Office"]
    # Busiest first: a 10-second place must not be presented beside an
    # hour-long one as though they were equal.
    assert summary[0].seconds == 600.0


def test_segments_without_a_place_are_skipped_not_bucketed() -> None:
    """The one thing this module must not do is invent a place.

    Their absence is already visible in the capture receipt; an "unknown"
    bucket here would look like a location we hold.
    """
    entries = [
        (None, 300.0),
        (CoarsePlace(lat=28.524, lon=77.185, label="Office"), 60.0),
    ]
    summary = summarise_places(entries)
    assert len(summary) == 1
    assert summary[0].name == "Office"
    assert summary[0].seconds == 60.0


def test_a_session_with_no_places_summarises_to_nothing() -> None:
    assert summarise_places([(None, 10.0), (None, 20.0)]) == []


def test_unpointed_places_group_by_name() -> None:
    """Two loose fixes labelled the same are one place, not two."""
    entries = [
        (CoarsePlace(locality="New Delhi"), 10.0),
        (CoarsePlace(locality="new delhi"), 20.0),
    ]
    summary = summarise_places(entries)
    assert len(summary) == 1
    assert summary[0].seconds == 30.0


def test_captured_at_is_preserved() -> None:
    when = dt.datetime(2026, 11, 21, 18, 0, tzinfo=dt.UTC)
    place = CoarsePlace.build(
        lat=PRECISE_LAT, lon=PRECISE_LON, captured_at=when
    )
    assert place is not None
    assert place.captured_at == when
