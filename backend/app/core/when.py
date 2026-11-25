"""Resolving time expressions in *questions*, into absolute ranges.

## Why this is not `core/dates.py`

`resolve_due` faces **forward**. It resolves deadlines, so bare "Friday" means
the coming Friday and "the day after tomorrow" is two days hence. That is
correct for a commitment and wrong for a question: "what did we decide on
Friday" is asking about a Friday that has already happened, and answering
about next Friday would return nothing while looking like a retrieval bug.

So the two parsers are mirror images, and they share only the weekday and
month tables. Everything here resolves **backward** from the asking time, and
produces a *range* rather than a date, because that is what a filter over
timestamped utterances needs.

## Ranges, not dates

"Yesterday" is a 24-hour window, "last week" is seven days, "in March" is a
month. A question never asks about an instant, so returning a date would push
the start/end arithmetic onto every caller - and the caller that got it
subtly wrong would silently answer about the wrong day.

## Ambiguity is reported, never resolved

"That day" has no meaning without knowing which day was just discussed. It
returns `candidates` for the caller to ask about rather than guessing at the
most recent one, because a wrong time range returns real sentences from the
wrong moment - which reads as a confident answer, not as a failure.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field

from app.core.dates import MONTHS, WEEKDAYS

#: Parts of a day, as narrowing windows in local hours [start, end).
DAY_PARTS: dict[str, tuple[int, int]] = {
    "morning": (5, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 24),
    "lunch": (12, 14),
    "lunchtime": (12, 14),
}


@dataclass
class TimeRange:
    """A half-open window, in the user's own timezone."""

    start: dt.datetime
    end: dt.datetime
    #: How a person would say it back, for the answer and for a clarification.
    label: str

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400


@dataclass
class ResolvedWhen:
    """What a question's time expression came to.

    `ranges` holds one entry when resolution was certain. More than one means
    the phrase was ambiguous and the caller should ask which - never pick.
    `None`/empty means the question carried no time expression at all, which
    is the ordinary case and not a failure.
    """

    ranges: list[TimeRange] = field(default_factory=list)
    phrase: str | None = None
    ambiguous: bool = False

    @property
    def resolved(self) -> TimeRange | None:
        return self.ranges[0] if len(self.ranges) == 1 else None


def _day(anchor: dt.datetime, day: dt.date, label: str) -> TimeRange:
    start = dt.datetime.combine(day, dt.time.min, tzinfo=anchor.tzinfo)
    return TimeRange(start, start + dt.timedelta(days=1), label)


def _narrow(window: TimeRange, part: str) -> TimeRange:
    """Clip a day-sized window to a part of the day.

    Only applied to windows of a day or less: "last month, in the morning"
    would otherwise collapse a month to a single morning, which is not what
    anyone means.
    """
    if window.days > 1.01:
        return window
    low, high = DAY_PARTS[part]
    base = window.start
    start = base.replace(hour=low, minute=0, second=0, microsecond=0)
    end = (
        base.replace(hour=0, minute=0, second=0, microsecond=0)
        + dt.timedelta(days=1)
        if high == 24
        else base.replace(hour=high, minute=0, second=0, microsecond=0)
    )
    return TimeRange(start, end, f"{window.label} {part}")


def _previous_weekday(anchor: dt.date, target: int) -> dt.date:
    """The most recent `target` weekday strictly before `anchor`.

    Strictly before, so "last Friday" asked *on* a Friday means the Friday a
    week ago rather than today. Asking about today is "today".
    """
    delta = (anchor.weekday() - target) % 7
    return anchor - dt.timedelta(days=delta or 7)


def resolve_when(
    text: str,
    *,
    now: dt.datetime,
    anchor_ranges: list[TimeRange] | None = None,
) -> ResolvedWhen:
    """Find a time expression in `text` and resolve it backward from `now`.

    `now` must be timezone-aware local time. `anchor_ranges` are the windows a
    previous answer was about, which is what gives "that day" a meaning; with
    none supplied, "that day" is reported ambiguous rather than guessed.
    """
    lowered = f" {text.lower().strip()} "
    today = now.date()

    def found(window: TimeRange, phrase: str) -> ResolvedWhen:
        for part in DAY_PARTS:
            if re.search(rf"\b{part}\b", lowered):
                window = _narrow(window, part)
                break
        return ResolvedWhen(ranges=[window], phrase=phrase)

    # --- phrases that name a day outright --------------------------------
    if re.search(r"\bday before yesterday\b", lowered):
        return found(
            _day(now, today - dt.timedelta(days=2), "the day before yesterday"),
            "day before yesterday",
        )
    if re.search(r"\byesterday\b", lowered):
        return found(_day(now, today - dt.timedelta(days=1), "yesterday"), "yesterday")
    if re.search(r"\btoday\b", lowered):
        return found(_day(now, today, "today"), "today")

    # --- "N days/weeks/months ago" ---------------------------------------
    match = re.search(r"\b(\d+)\s+(day|week|month)s?\s+ago\b", lowered)
    if match:
        count, unit = int(match.group(1)), match.group(2)
        if unit == "day":
            day = today - dt.timedelta(days=count)
            return found(_day(now, day, f"{count} days ago"), match.group(0))
        span = dt.timedelta(weeks=count) if unit == "week" else dt.timedelta(days=30 * count)
        start = dt.datetime.combine(today, dt.time.min, tzinfo=now.tzinfo) - span
        return ResolvedWhen(
            ranges=[
                TimeRange(start, start + dt.timedelta(days=7 if unit == "week" else 30),
                          f"{count} {unit}s ago")
            ],
            phrase=match.group(0),
        )

    # --- weekdays ---------------------------------------------------------
    names = "|".join(WEEKDAYS)
    match = re.search(rf"\b(?:last|this|past)?\s*({names})\b", lowered)
    if match:
        target = WEEKDAYS[match.group(1)]
        # Always the most recent one: a question is about the past, so bare
        # "Friday" is the Friday that happened, not the one coming.
        day = today if today.weekday() == target and "last" not in lowered else _previous_weekday(today, target)
        label = match.group(1).capitalize()
        return found(_day(now, day, label), match.group(0).strip())

    # --- "this morning" and friends ---------------------------------------
    # A part of the day with no day named means today. A BARE part ("in the
    # morning") is left unresolved on purpose: it needs a day, and picking
    # today would answer about the wrong morning whenever the question was
    # about a conversation from last week.
    for part in DAY_PARTS:
        if re.search(r"\bthis " + part + r"\b", lowered):
            return ResolvedWhen(
                ranges=[_narrow(_day(now, today, "today"), part)],
                phrase=f"this {part}",
            )

    # --- weeks and months -------------------------------------------------
    if re.search(r"\blast week\b", lowered):
        start_of_week = today - dt.timedelta(days=today.weekday() + 7)
        start = dt.datetime.combine(start_of_week, dt.time.min, tzinfo=now.tzinfo)
        return ResolvedWhen(
            ranges=[TimeRange(start, start + dt.timedelta(days=7), "last week")],
            phrase="last week",
        )
    if re.search(r"\bthis week\b", lowered):
        start_of_week = today - dt.timedelta(days=today.weekday())
        start = dt.datetime.combine(start_of_week, dt.time.min, tzinfo=now.tzinfo)
        return ResolvedWhen(
            ranges=[TimeRange(start, start + dt.timedelta(days=7), "this week")],
            phrase="this week",
        )
    if re.search(r"\blast month\b", lowered):
        first = today.replace(day=1)
        previous_end = first
        previous_start = (first - dt.timedelta(days=1)).replace(day=1)
        return ResolvedWhen(
            ranges=[
                TimeRange(
                    dt.datetime.combine(previous_start, dt.time.min, tzinfo=now.tzinfo),
                    dt.datetime.combine(previous_end, dt.time.min, tzinfo=now.tzinfo),
                    "last month",
                )
            ],
            phrase="last month",
        )

    # --- a named month, optionally with a day -----------------------------
    month_names = "|".join(MONTHS)
    match = re.search(rf"\b({month_names})\s*(\d{{1,2}})?\b", lowered)
    if match:
        month = MONTHS[match.group(1)]
        # Backward: a month later than the current one must be last year.
        year = now.year if month <= now.month else now.year - 1
        if match.group(2):
            try:
                day = dt.date(year, month, int(match.group(2)))
            except ValueError:
                return ResolvedWhen(phrase=match.group(0))
            return found(_day(now, day, f"{day:%d %B}"), match.group(0))
        last = calendar.monthrange(year, month)[1]
        return ResolvedWhen(
            ranges=[
                TimeRange(
                    dt.datetime(year, month, 1, tzinfo=now.tzinfo),
                    dt.datetime(year, month, last, tzinfo=now.tzinfo)
                    + dt.timedelta(days=1),
                    f"{calendar.month_name[month]} {year}",
                )
            ],
            phrase=match.group(1),
        )

    # --- deictic: needs an anchor ----------------------------------------
    if re.search(r"\b(that day|then|that time|that morning|that evening)\b", lowered):
        phrase = re.search(
            r"\b(that day|then|that time|that morning|that evening)\b", lowered
        ).group(1)
        if anchor_ranges:
            if len(anchor_ranges) == 1:
                return ResolvedWhen(ranges=list(anchor_ranges), phrase=phrase)
            # Several candidates: the caller asks which, rather than us
            # silently answering about one of them.
            return ResolvedWhen(
                ranges=list(anchor_ranges), phrase=phrase, ambiguous=True
            )
        return ResolvedWhen(phrase=phrase, ambiguous=True)

    return ResolvedWhen()
