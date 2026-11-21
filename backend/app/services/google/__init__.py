"""Google execution drivers (phase 10).

`oauth.py` is the only module that touches tokens. `calendar.py` and
`gmail.py` are executors satisfying the same `ActionExecutor` Protocol as the
local ones, so approval, idempotency and action history did not change when
real side effects arrived.

The product rule that shaped all of it: **approval authorises writing to the
user's own calendar, and drafting — never sending.**

By default that is guaranteed by what is requested: the scope list is
Calendar only, so there is no token capable of touching the user's mail at
all. Gmail drafting is opt-in via `GOOGLE_ENABLE_GMAIL`, and `gmail.py`
records why - the `gmail.compose` scope permits sending, so the original
"structurally incapable" claim was wrong and had to be corrected.
"""

from app.services.google.calendar import (
    GoogleCalendarExecutor,
    GoogleReminderExecutor,
)
from app.services.google.gmail import GmailDraftExecutor
from app.services.google.oauth import (
    GoogleNotConfigured,
    GoogleOAuthService,
    GoogleTokenError,
)

__all__ = [
    "GmailDraftExecutor",
    "GoogleCalendarExecutor",
    "GoogleNotConfigured",
    "GoogleOAuthService",
    "GoogleReminderExecutor",
    "GoogleTokenError",
]
