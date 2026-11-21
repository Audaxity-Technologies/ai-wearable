"""Coarse place capture.

## Why this exists now, before anything uses it

Everything else in the memory plan can be rebuilt later by re-running a
pipeline over stored audio: the utterance index, entity resolution, embeddings,
summaries. **Location cannot.** There is no way to recover where someone was
in March 2026 if nobody wrote it down in March 2026.

So this ships before the retrieval that needs it, which is unusual and
deliberate. The alternative is a memory product that can never answer "that
conversation at the cafe".

## Coarse means coarse, and this is where that is enforced

Precise coordinates buy nothing for retrieval and cost a great deal in a
breach. People do not ask "what did I say at 28.5245, 77.1855" - they ask
about *the cafe*, *the office*, *when I was in Delhi*. What retrieval needs is
something **clusterable** (so two recordings at the same spot group together)
and something **nameable**. Neither requires a trace.

Two mechanisms:

1. **Rounding to a fixed grid.** `PLACE_PRECISION_DP` decimal places, default
   3, which is about 110m at the equator - enough to tell one building from
   the next, not enough to follow someone down a street.
2. **Rounding happens twice.** The client rounds before sending, so precise
   coordinates never leave the device. The server rounds again on write, so a
   careless or older client cannot deposit precision we promised not to keep.
   The second one is the one that actually holds, because it does not depend
   on the client being correct.

What is deliberately NOT stored: a heading, a speed, an altitude, an accuracy
radius tighter than the grid, or any point for a moment when no audio was
being recorded. A sequence of coarse points during recordings is a record of
where conversations happened. A continuous one would be a movement history,
which is a different product and a different liability.

## Opt-in, not opt-out

`Segment.place` is `None` for everything recorded before the user turns this
on, and `None` is a first-class value throughout - exactly as `owner` is
`"unknown"`. Nothing here infers a location from an IP address or from a
previous segment: a guessed place would corrupt the one axis of the index we
cannot re-derive.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator

#: Decimal places kept on latitude/longitude. 3dp is ~110m at the equator.
#: Raising this is a privacy decision, not a tuning knob: 5dp is ~1m, which
#: turns a place record into a movement trace.
PLACE_PRECISION_DP = 3

#: Any client-reported accuracy looser than this makes the fix useless for
#: distinguishing one place from another, so it is recorded as a region-level
#: label only and the coordinates are dropped.
MAX_USEFUL_ACCURACY_M = 2000.0


def round_coordinate(value: float, dp: int = PLACE_PRECISION_DP) -> float:
    """Snap a coordinate to the retention grid.

    Applied on every write, including to data a client claims is already
    rounded. The client rounds so precision never leaves the device; the
    server rounds so the promise does not depend on the client.
    """
    return round(value, dp)


class CoarsePlace(BaseModel):
    """Roughly where a recording happened. Never precisely."""

    #: Rounded to `PLACE_PRECISION_DP`. Both or neither: a lone latitude is
    #: not a place.
    lat: float | None = None
    lon: float | None = None

    #: Human-readable, as the user or a geocoder would say it. Free text
    #: because "Pulkit's place" is as valid an answer as "Hauz Khas".
    label: str | None = None
    locality: str | None = None
    region: str | None = None
    country: str | None = None

    #: "device" (from the OS), "manual" (the user named it), or "inferred"
    #: - which nothing currently produces, and which must never be produced
    #: by guessing from a previous segment.
    source: str = "device"

    #: What the OS claimed, before rounding. Kept because a 1500m fix and a
    #: 20m fix are the same grid cell but not the same evidence.
    reported_accuracy_m: float | None = None
    captured_at: dt.datetime | None = None

    @field_validator("lat")
    @classmethod
    def _valid_lat(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not -90.0 <= value <= 90.0:
            raise ValueError("latitude must be between -90 and 90")
        return round_coordinate(value)

    @field_validator("lon")
    @classmethod
    def _valid_lon(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not -180.0 <= value <= 180.0:
            raise ValueError("longitude must be between -180 and 180")
        return round_coordinate(value)

    @field_validator("label", "locality", "region", "country")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    @property
    def has_point(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def is_empty(self) -> bool:
        """Nothing worth storing.

        A `CoarsePlace` with no point and no names is not a place; callers
        store `None` instead, so that "we have no location" is one state
        rather than two.
        """
        return not self.has_point and not any(
            (self.label, self.locality, self.region, self.country)
        )

    @property
    def grid_key(self) -> str | None:
        """Stable key for grouping recordings made in the same cell.

        This is what makes "that conversation at the cafe" answerable without
        a geocoder: two sessions in the same cell are the same place, whatever
        either was labelled.
        """
        if not self.has_point:
            return None
        return f"{self.lat:.{PLACE_PRECISION_DP}f},{self.lon:.{PLACE_PRECISION_DP}f}"

    def describe(self) -> str:
        """One short phrase, in the user's terms. Never coordinates.

        Coordinates are an index, not an answer: showing "28.524, 77.186"
        where a name belongs would be technically honest and practically
        useless.
        """
        for value in (self.label, self.locality, self.region, self.country):
            if value:
                return value
        return "an unnamed place"

    @classmethod
    def build(
        cls,
        *,
        lat: float | None = None,
        lon: float | None = None,
        label: str | None = None,
        locality: str | None = None,
        region: str | None = None,
        country: str | None = None,
        source: str = "device",
        reported_accuracy_m: float | None = None,
        captured_at: dt.datetime | None = None,
    ) -> CoarsePlace | None:
        """Build a place, or `None` when there is nothing to record.

        Drops the point - keeping any names - when:
          * only one of lat/lon was supplied, because half a coordinate is
            not a location; or
          * the fix was looser than `MAX_USEFUL_ACCURACY_M`, because a 5km
            fix cannot distinguish two places and storing it as though it
            could is worse than storing nothing.
        """
        if (lat is None) != (lon is None):
            lat = lon = None
        if (
            reported_accuracy_m is not None
            and reported_accuracy_m > MAX_USEFUL_ACCURACY_M
        ):
            lat = lon = None

        place = cls(
            lat=lat,
            lon=lon,
            label=label,
            locality=locality,
            region=region,
            country=country,
            source=source,
            reported_accuracy_m=reported_accuracy_m,
            captured_at=captured_at,
        )
        return None if place.is_empty else place


class SessionPlace(BaseModel):
    """A place a session visited, with how much of it happened there.

    A session is not at one place: you walk from the office to a cafe
    mid-conversation. The per-segment record is the truth; this is the
    summary, and it keeps `seconds` so a 10-second place is not presented
    beside an hour-long one as though they were equal.
    """

    place: CoarsePlace
    seconds: float = 0.0
    segment_count: int = 0

    @property
    def name(self) -> str:
        return self.place.describe()


def summarise_places(
    entries: list[tuple[CoarsePlace | None, float]],
) -> list[SessionPlace]:
    """Group (place, duration) pairs into a per-session summary.

    Grouped by `grid_key` where there is a point, and by name otherwise, so
    two differently-labelled recordings in one cell do not become two places.
    Segments with no place are skipped rather than lumped into an "unknown"
    bucket: their absence is already visible in the capture receipt, and
    inventing a place for them is the one thing this module must not do.
    """
    buckets: dict[str, SessionPlace] = {}
    for place, seconds in entries:
        if place is None:
            continue
        key = place.grid_key or f"name:{place.describe().lower()}"
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = SessionPlace(
                place=place, seconds=seconds, segment_count=1
            )
            continue
        existing.seconds += seconds
        existing.segment_count += 1
        # Prefer a named place over an unnamed one when merging: the label is
        # the part a person can recognise.
        if existing.place.label is None and place.label is not None:
            existing.place = place

    return sorted(
        buckets.values(), key=lambda entry: entry.seconds, reverse=True
    )
