"""Deterministic resolution of relative date phrases.

The LLM returns the verbatim phrase ("by Friday"); this module turns it into a
date using the session's own start time and timezone. Date arithmetic is the
single most user-visible class of error in this product, so it lives in code
that can be unit-tested rather than in a prompt.

Guiding rule: when a phrase is genuinely ambiguous in English, return
AMBIGUOUS rather than guessing. The app then asks the user instead of silently
putting the wrong day on a calendar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.models.analysis import DateResolution

WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

#: Leading words that carry no date information.
_LEADING_NOISE = re.compile(
    r"^(?:by|before|on|at|due|until|till|no later than|sometime|the)\s+"
)

_NUMBER_WORDS: dict[str, int] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


@dataclass(frozen=True)
class ResolvedDate:
    date: date | None
    time: time | None
    resolution: DateResolution


def _clean(raw: str) -> str:
    text = raw.strip().lower().rstrip(".!?,;:")
    text = re.sub(r"\s+", " ", text)
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_NOISE.sub("", text)
    return text.strip()


def _extract_time(text: str) -> tuple[time | None, str, bool]:
    """Pull a clock time out of `text`.

    Returns `(time, remaining_text, vague)`. `vague` marks words like "morning"
    that imply a time of day without pinning it down.
    """
    # 5pm / 5 pm / 5:30pm / 5.30 pm / 5 p.m. / 5 P.M.
    # The dotted form matters: Whisper transcribes spoken "5 PM" as "5 p.m.",
    # so a regex that only accepts "pm" silently loses the time.
    match = re.search(
        r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\s*\.?\s*m\.?(?!\w)", text
    )
    if match:
        hour = int(match.group(1)) % 12
        minute = int(match.group(2) or 0)
        if match.group(3) == "p":
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute), _strip(text, match), False

    # 24-hour: 17:00
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute), _strip(text, match), False

    match = re.search(r"\bnoon|midday\b", text)
    if match:
        return time(12, 0), _strip(text, match), False
    match = re.search(r"\bmidnight\b", text)
    if match:
        return time(0, 0), _strip(text, match), False

    match = re.search(r"\b(morning|afternoon|evening|tonight|night)\b", text)
    if match:
        # A time of day was mentioned but not a clock time.
        return None, text, True

    return None, text, False


def _strip(text: str, match: re.Match[str]) -> str:
    out = text[: match.start()] + " " + text[match.end() :]
    return re.sub(r"\s+", " ", out).replace(" at ", " ").strip()


def _next_weekday(anchor: date, target: int, *, strictly_after: bool) -> date:
    delta = (target - anchor.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return anchor + timedelta(days=delta)


def _month_day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve_due(raw: str | None, anchor: datetime) -> ResolvedDate:
    """Resolve `raw` against `anchor`, the session start in the user's own zone.

    `anchor` must be timezone-aware local time (see `time_utils.session_now`).
    """
    if raw is None or not raw.strip():
        return ResolvedDate(None, None, DateResolution.NONE)

    text = _clean(raw)
    if not text:
        return ResolvedDate(None, None, DateResolution.NONE)

    clock, text, vague_time = _extract_time(text)
    today = anchor.date()

    def done(value: date | None, resolution: DateResolution) -> ResolvedDate:
        # A vague time of day ("Friday morning") keeps the date but cannot be
        # treated as confident enough to schedule to the minute.
        if value is not None and vague_time and clock is None:
            return ResolvedDate(value, None, DateResolution.AMBIGUOUS)
            # NOTE: the date is still returned so the app can show it.
        return ResolvedDate(value, clock, resolution)

    # --- explicit ISO date -------------------------------------------------
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if match:
        value = _month_day(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        )
        if value:
            return done(value, DateResolution.CONFIDENT)

    # --- "November 21" / "21 November" / "Nov 21st 2026" -------------------
    month_names = "|".join(MONTHS)
    match = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b",
        text,
    )
    if not match:
        match = re.search(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_names})"
            rf"(?:,?\s+(\d{{4}}))?\b",
            text,
        )
        if match:
            day, month_name, year = (
                match.group(1),
                match.group(2),
                match.group(3),
            )
        else:
            day = month_name = year = None
    else:
        month_name, day, year = match.group(1), match.group(2), match.group(3)

    if month_name and day:
        month = MONTHS[month_name]
        value = _month_day(int(year) if year else today.year, month, int(day))
        if value:
            # No year given and the date already passed: assume next year.
            if not year and value < today:
                value = _month_day(today.year + 1, month, int(day)) or value
            return done(value, DateResolution.CONFIDENT)

    # --- day offsets -------------------------------------------------------
    if re.search(r"\bday after tomorrow\b", text):
        return done(today + timedelta(days=2), DateResolution.CONFIDENT)
    if re.search(r"\btomorrow\b", text):
        return done(today + timedelta(days=1), DateResolution.CONFIDENT)
    if re.search(r"\byesterday\b", text):
        return done(today - timedelta(days=1), DateResolution.CONFIDENT)
    if re.search(r"\b(today|tonight|this evening|this afternoon|this morning)\b", text):
        return done(today, DateResolution.CONFIDENT)

    # "in 3 days", "in two weeks", "3 days from now"
    match = re.search(
        r"\b(?:in|within|after)\s+(\d+|%s)\s+(day|week|month)s?\b"
        % "|".join(_NUMBER_WORDS),
        text,
    ) or re.search(
        r"\b(\d+|%s)\s+(day|week|month)s?\s+from now\b" % "|".join(_NUMBER_WORDS),
        text,
    )
    if match:
        token = match.group(1)
        count = int(token) if token.isdigit() else _NUMBER_WORDS[token]
        unit = match.group(2)
        if unit == "day":
            return done(today + timedelta(days=count), DateResolution.CONFIDENT)
        if unit == "week":
            # "in a week" is a point; "in two weeks" is still a point.
            return done(today + timedelta(weeks=count), DateResolution.CONFIDENT)
        # Months vary in length; treat as a range the user should confirm.
        return done(None, DateResolution.AMBIGUOUS)

    # --- vague windows -----------------------------------------------------
    # These imply a deadline exists without saying when. The brief is explicit
    # that these must surface as "needs date clarification".
    if re.search(
        r"\b(next week|next month|next year|this week|this month|"
        r"end of (?:the )?(?:week|month|quarter|year)|"
        r"later this week|sometime|soon|asap|in a bit|eventually|"
        r"the coming week|coming weeks|next few days|couple of days)\b",
        text,
    ):
        return done(None, DateResolution.AMBIGUOUS)

    # --- weekdays ----------------------------------------------------------
    weekday_names = "|".join(WEEKDAYS)
    match = re.search(rf"\b(next|this|coming)?\s*({weekday_names})\b", text)
    if match:
        qualifier = (match.group(1) or "").strip()
        target = WEEKDAYS[match.group(2)]
        if qualifier == "next":
            # "next Friday" means this coming Friday to some speakers and the
            # one after to others. Genuinely ambiguous: do not pick one.
            return done(
                _next_weekday(today, target, strictly_after=True),
                DateResolution.AMBIGUOUS,
            )
        if today.weekday() == target:
            # Said on a Friday, "by Friday" may mean today or a week out.
            return done(today, DateResolution.AMBIGUOUS)
        return done(
            _next_weekday(today, target, strictly_after=True),
            DateResolution.CONFIDENT,
        )

    # --- weekend -----------------------------------------------------------
    if re.search(r"\b(this )?weekend\b", text):
        return done(
            _next_weekday(today, 5, strictly_after=True), DateResolution.AMBIGUOUS
        )

    # A bare clock time with no date: probably today, but don't assume.
    if clock is not None:
        return ResolvedDate(None, clock, DateResolution.AMBIGUOUS)
    if vague_time:
        return ResolvedDate(None, None, DateResolution.AMBIGUOUS)

    return ResolvedDate(None, None, DateResolution.NONE)
