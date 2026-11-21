# Audaxity Backend — Phases 0–6, plus auth and notifications

Ingest, storage, transcription and conversation analysis. A session now ends at
`ready` with a structured analysis: items classified by speech act, owners
attributed, and dates resolved against the session's own timezone.

See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the full design.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt   # macOS/Linux
cp .env.example .env
```

Get a free Groq API key at <https://console.groq.com> and put it in `.env` as
`GROQ_API_KEY`. One key covers both transcription and analysis. To build
against stubs instead — no key, no quota used — set
`TRANSCRIPTION_PROVIDER=fake` and `LLM_PROVIDER=fake`.

## Run

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters: it is what lets your phone reach the server on the
same Wi-Fi. Interactive API docs are at <http://localhost:8000/docs>.

**Run exactly one worker.** The JSON store is single-process safe only.

## Test

```bash
.venv/Scripts/python -m pytest -q
```

## Quick manual check

```bash
curl -s localhost:8000/v1/health
```

Then open a session, upload a segment, close it, and read the transcript:

```bash
curl -s -X POST localhost:8000/v1/sessions -H 'Content-Type: application/json' -d '{"tz":"Asia/Kolkata"}'
```

```bash
curl -s -X POST localhost:8000/v1/ingest/audio -F "file=@clip.wav;type=audio/wav" --form-string "segment_id=$(python -c 'import uuid;print(uuid.uuid4())')" --form-string "session_id=<SESSION_ID>" --form-string "seq=0" --form-string "started_at=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" --form-string "duration_s=5.0"
```

```bash
curl -s -X POST localhost:8000/v1/sessions/<SESSION_ID>/close
```

```bash
curl -s localhost:8000/v1/sessions/<SESSION_ID>/transcript
```

```bash
curl -s localhost:8000/v1/sessions/<SESSION_ID>/analysis
```

## The eval set — read this before touching a prompt

`app/services/llm/prompts.py` is a product file. Editing it without running the
eval set regresses owner attribution and speech-act classification silently.

```bash
.venv/Scripts/python scripts/run_eval.py
```

One LLM call per case, four cases. It prints a scorecard and exits non-zero if
any case fails:

```
cases passed    3/4
items found     11/12 (92%)
act correct     10/12 (83%)
owner correct   12/12 (100%)   <- the costliest error
date correct    11/12 (92%)
spurious items  1   <- over-extraction
```

Run it before and after every prompt change, and compare. A case may list
`tolerated_spurious`: a borderline extraction accepted on purpose, recorded
with its reasoning so the suite stays green and keeps catching real
regressions. Cases live in `evals/cases/*.json`:

| Case | What it catches |
|---|---|
| `cofounder_sync` | The brief's canonical example, four speech acts at once |
| `speech_acts` | The same action phrased four ways — the core distinction |
| `small_talk` | Over-extraction: chat must not become tasks (one borderline item accepted, see its `decision_note`) |
| `personal_schedule` | Personal commitments — built from a real device recording the prompt once discarded entirely |
| `ambiguous_dates` | Invented deadlines: "next week"/"soon" must not get a date |

**Add your own cases.** Record 10 real conversations between the two of you,
label the expected tasks, owners and dates, and drop them in. Labelled quotes
must appear verbatim in the transcript — `tests/test_evals.py` enforces that,
because an eval set with bad labels reports confident numbers about nothing.

## How it fits together

| Layer | Rule |
|---|---|
| `api/` | Validates, calls a service, maps to a DTO. No logic. |
| `services/` | All behaviour. Never imports FastAPI. |
| `repositories/` | Storage behind a Protocol. Swap JSON for SQL here only. |
| `models/domain.py` | What is persisted. |
| `models/dto.py` | What the API exposes. Deliberately separate. |

### Things worth knowing

- **Ingest is idempotent on `segment_id`.** A retried upload returns
  `duplicate: true` and creates nothing. Clients should retry freely.
- **Segments are transcribed as they arrive**, in parallel with recording.
  Closing the session finalizes the transcript in `seq` order, so uploads may
  arrive out of order.
- **One bad segment does not lose the conversation.** It is marked `failed` and
  the remaining segments still produce a transcript.
- **Retries**: retryable driver errors (429, 5xx, timeouts) are retried up to
  `JOB_MAX_ATTEMPTS`; a missing API key is not retried.
- **Crash recovery**: unfinished jobs are re-queued on startup from the
  persisted `Job` records.
- **Timezones**: clients send an IANA name and an offset-aware `started_at`.
  Naive timestamps are rejected. On Windows this needs the `tzdata` package,
  which is in `requirements.txt`.
- **Logging never contains transcript text** — identifiers and counts only.

### Analysis guarantees (phase 3)

- **Schema-first.** The LLM response is validated against `RawAnalysis`. A
  failure gets exactly one repair attempt with the validator error fed back.
- **A bad response never costs you the conversation.** If repair also fails the
  session is marked `analyzed_degraded` and the transcript is kept.
- **Nothing invented.** Every item's `source_quote` is verified against the
  transcript; items that fail are dropped and listed in `dropped`, so prompt
  regressions are visible rather than silent.
- **Dates are resolved in Python, never by the LLM** (`app/core/dates.py`).
  The model returns the phrase ("by Friday"); the resolver converts it using
  the session's start time and timezone. Genuinely ambiguous phrases ("next
  week", "next Friday", "soon") resolve to `ambiguous` so the app can ask
  instead of scheduling a guess.
- **`owner: "unknown"` is a correct answer.** Inventing an owner tells someone
  they promised something they never said.
- Analysis is a **separate job** from transcription, so an LLM outage retries
  on its own and never re-runs transcription.
- `POST /v1/sessions/{id}/analyze` re-runs analysis on an existing transcript —
  one LLM call, no re-transcription. This is the prompt-iteration loop.

### Memory guarantees (phase 4)

Memory is entity-centric: one row per person, project or durable fact,
accumulating evidence across conversations.

- **Selective.** Only people, projects, and *durable* facts (commitments,
  assignments, requirements, decisions) are stored. Suggestions, open
  questions and general discussion are deliberately excluded — a floated idea
  is not a fact, and promoting one is how an assistant starts confidently
  telling you things you never agreed to.
- **Deduplicated across conversations.** The same project on Monday and
  Wednesday is one memory with two sources, a higher confidence and a bumped
  `last_seen`. "Manan" and "Manan Dudeja" resolve to one person; the fuller
  name wins.
- **Conflicts preserved, never overwritten.** When a later conversation
  changes a deadline or an owner, both versions survive and reference each
  other via `conflicts_with`. `GET /v1/memory?conflicts_only=true` is the
  review queue.
- **Two ways a conflict is found.** Same-key facts with a changed date or
  owner conflict directly. For changes worded differently from the original
  ("investor pitch deadline moved" vs "investor pitch ready by Friday") the
  extractor emits a `supersedes` hint and memory matches on that. With no
  hint and no clear match, the item is simply stored — a missed conflict is a
  far smaller harm than falsely telling someone two unrelated commitments
  contradict each other.
- **Provenance on everything.** `source_session_ids` and `source_quotes`, so
  "why do you believe this?" is always answerable.
- **Deletion respects shared memories.** Deleting a conversation removes
  memories only it supported; one supported by others survives with that
  session detached.

Endpoints: `GET /v1/memory`, `GET /v1/memory/search?q=`,
`GET /v1/memory/{id}`, `DELETE /v1/memory/{id}`.

### Tasks, actions and approval (phase 5)

**Nothing executes without explicit approval.** This is enforced structurally,
not by convention: the action state machine permits `EXECUTING` only from
`APPROVED`, and only `approve()` performs that transition. The action engine
has no access to an executor at all, so a bug there cannot send anything.

**Nothing executes twice.** Approval mints an idempotency key and the approval
service holds a per-action lock across approval *and* execution, so a
double-tap, a retried request or three concurrent approvals all execute once.
There is a test that fires three simultaneous approvals and asserts a single
`executed` event.

- **Tasks** come only from actionable acts (commitment, assignment,
  requirement). Suggestions, questions and decisions never become tasks.
  Re-discussing a commitment updates one task instead of creating a second.
- **Overdue requires a confident date.** An ambiguous "next week" is never
  reported as overdue, because we do not know when it was due.
- **Action type follows what was actually established**: confident date and
  time → calendar event; confident date only → reminder; ambiguous date →
  reminder flagged `needs_clarification`; no date → plain task.
- **Every action carries its reason and its quote**, so the approval card can
  answer "why is this here?".
- **Rejections stick.** A rejected action is terminal and is not re-proposed
  from a later conversation — re-asking every time the topic comes up is
  nagging.
- **Edits re-derive the type.** Adding a date to a task action makes it a
  reminder; adding a time makes it a calendar event. Otherwise the executor
  would silently discard the detail the user just supplied.
- **Refuses rather than guesses.** A calendar event without a time fails with
  a clear message instead of inventing a slot.
- **Executed actions survive session deletion** — they record something that
  already happened.

What "executed" means today: the action is recorded locally and surfaced in
the app. **Nothing leaves the machine.** Google Calendar and Gmail drivers
satisfy the same executor Protocol in phase 10, and approval, idempotency and
history do not change when they arrive. `EMAIL_DRAFT` is defined but never
proposed; approving one fails loudly rather than pretending.

Endpoints: `GET /v1/tasks`, `PATCH /v1/tasks/{id}`,
`GET /v1/actions/pending`, `GET /v1/actions`, `PATCH /v1/actions/{id}`,
`POST /v1/actions/{id}/approve`, `POST /v1/actions/{id}/reject`.

### Ask (phase 6)

Grounded question answering over conversations, memories and tasks.
`POST /v1/ask`.

- **No retrieval means no model call.** When nothing relevant is found the
  service answers "I don't have anything about that" and never reaches the
  LLM. That is cheaper, deterministic, and removes the one situation where a
  model is most likely to invent something: being asked a question with no
  material to work from.
- **Citations are verified.** The model cites sources by *number*, not UUID —
  asking a model to reproduce a 36-character identifier invites silent
  transcription errors. Any number outside the supplied range is dropped and
  reported in `dropped_citations`, so a citation can never tap through to a
  conversation that does not support the claim.
- **An answer citing nothing is not grounded**, and is returned with
  `confident: false`.
- **Conflicts are surfaced, not resolved.** A memory with a contradiction
  ranks higher and is marked in the prompt, and the answer is expected to give
  both versions and note which is newer.
- **An LLM outage still returns the matched sources**, because "the service is
  down" must never look like "you never said that".
- **Retrieval is keyword and metadata based**, per the architecture doc. It is
  debuggable — every hit records which terms matched — and it has no index to
  keep in sync. Questions about outstanding work ("what's still pending?")
  surface open tasks on intent even when no task text matches.

## Verified against live Groq (2026-11-19)

Both Groq drivers have been run end to end against the real API: synthesized
speech -> `whisper-large-v3-turbo` -> `openai/gpt-oss-120b` -> structured
analysis, through the HTTP API.

**Model availability.** `llama-3.3-70b-versatile` returns 404 — it is not in
the account's catalogue. Chat models actually served: `openai/gpt-oss-120b`
(the default, and the strongest here), `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`,
`allam-2-7b`. Both Whisper models are available. Check with:

```bash
curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
```

**Rate limit that bites: 8000 tokens/minute** on the free tier. One analysis
call costs roughly 2000 tokens, so four back-to-back calls hit a 429. The eval
runner paces itself (`--delay`, default 20s) and backs off on 429. For the
mobile app this matters once several conversations finish close together: the
pipeline's job-level retry handles it, but throughput is capped at roughly
3-4 analyses per minute.

**Eval baseline** (`openai/gpt-oss-120b`, 6 cases / 21 labelled items):

```
cases passed    5/6
items found     21/21 (100%)
act correct     21/21 (100%)
owner correct   21/21 (100%)
date correct    21/21 (100%)
spurious items  2
```

The one failing case is a genuine product question, not a model error:
"Should we send the deck too?" / "Let's not decide that now." The model keeps
it as an open question and records the postponement; the label says a declined
item is not a task. **Decide whether a deferred decision should resurface**,
then fix either the label or the prompt. Do not quietly change the label to
match the output.


**Ask, verified live.** After two real recorded conversations, asking "When
is the investor pitch due?" produced:

> There is a conflict in the recorded dates: earlier notes say the investor
> pitch should be ready by Friday (2026-11-20), but a later update says the
> deadline was moved to ... 2026-11-21. The most recent information points to
> a due date of 2026-11-21.

— the contradiction preserved in phase 4 surfacing as a caveated answer rather
than a confident wrong one. "What did we decide about the submarine contract?"
returned "I don't have anything about that in your conversations" with
`grounded: false`.

**Approval workflow, verified live.** A real recording produced two tasks
(the suggestion in the same conversation correctly did *not* become one) and
two proposed actions, each with its reason and quote. Approving executed once;
approving again left `executed_at` unchanged with a single `executed` event in
the history; a rejected action returned HTTP 409 on re-approval.

**Cross-conversation memory, verified live.** Two separate recorded
conversations about the same projects produced one merged `Investor pitch`
memory (`mention_count: 2`, both sessions as sources), and the deliberately
planted deadline change surfaced as a conflict with both versions intact:

```
Investor pitch ready          due=2026-11-20  "...needs to be ready by Friday."
Investor pitch deadline moved due=2026-11-21  "...now due the day after tomorrow, not Friday."
```

Two caveats worth knowing:

- **Owner attribution needs speaker labels, and this is the biggest open
  risk.** On real audio where speakers announced themselves, Whisper
  transcribed "Pulkit here" as "Pull kit here" — so Pulkit vanished from the
  participant list and a commitment *he* made ("I will order the enclosure")
  was attributed to **Manan**, the last cleanly-named speaker. The extraction
  layer behaved correctly given its input; the input was wrong. Spoken name
  prefixes are not a substitute for diarization, and the band will need real
  speaker separation before owner attribution can be trusted. The eval cases
  carry clean `Name:` prefixes, which is why they score 100%.
- **Project recall varies.** In the live run, conversation 2 mentioned the
  wearable prototype but the model did not list it under `projects`, so that
  memory stayed at one mention instead of two. Merging works; what reaches it
  depends on extraction recall.
- **Runs vary.** The same transcript classified a meeting as `suggestion` on
  one run and `decision` on another. Both are defensible; treat single-run
  eval deltas of one item as noise.

### Auth, notifications and the wake-up call

**Auth is on by default.** Every endpoint resolves the acting user from a
bearer token, and every per-session route checks ownership — a session you do
not own reports as missing rather than forbidden, because a 403 would confirm
the id exists. Passwords use `hashlib.scrypt` (stdlib, no native build).

`AUTH_REQUIRED=false` falls back to a `?user_id=` query parameter for local
work. Production refuses to start with it off, with the development signing
secret, or with a secret under 32 characters.

**A wake-up call does not start the microphone.** `POST /v1/wake` sends an
interrupting notification on its own Android channel; the app opens the Record
screen showing the reason, and recording begins only when the user presses the
button. There is a test asserting that a wake request creates no session.
Remote-triggered capture would be covert recording, which the brief forbids
and Android 14+ blocks from the background regardless.

**The daily briefing** is sent at a time the user picks, in their own
timezone: due today, overdue, needing a date, and how many approvals wait. An
in-process loop checks every minute and sends once per local day. A five-minute
window means a missed tick still fires.

Endpoints: `/v1/auth/register`, `/v1/auth/login`, `/v1/auth/me`,
`/v1/devices`, `/v1/wake`, `/v1/briefing`, `/v1/briefing/send`.

## Not done yet
- **The Gemini driver has never made a live call.** Its request shape is
  written to Google's documented API but is unverified, and
  `gemini-2.5-flash` is an unchecked default.
- No speaker diarization, so owners come only from in-transcript name labels.
- No SSE endpoint yet (the app can poll `GET /v1/sessions?updated_since=`).
- Ask is stateless: questions and answers are not stored, so there is no
  conversation history or follow-up ("what about the other one?").
- Retrieval will miss paraphrases — "the pitch" will not match "investor
  deck". Add project alias lists before reaching for embeddings.
- **Known debt: a task's dedupe key includes its owner.** If you correct an
  owner, a later conversation that re-extracts the original owner creates a
  second task rather than updating yours. Your edit is never overwritten, but
  you can end up with a duplicate. Fix when owner correction becomes common.
- Executors are local only. Nothing is sent to Google Calendar or Gmail, and
  `EMAIL_DRAFT` actions are never proposed.

## Google Calendar and Gmail (phase 10)

With no credentials configured the backend behaves exactly as it did in phase
5: approved actions are recorded locally. Phase 10 only activates once an
OAuth client exists.

### Setting it up

1. In Google Cloud Console, create a project and enable the **Google Calendar
   API** and **Gmail API**.
2. Create an **OAuth 2.0 Client ID** of type *Web application*.
3. Add the redirect URI **exactly** as the backend will send it, character for
   character, or Google answers `redirect_uri_mismatch`:

   ```
   https://<your-tunnel-host>/v1/integrations/google/callback
   ```

4. Put the credentials in `backend/.env`:

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_REDIRECT_URI=https://<your-tunnel-host>/v1/integrations/google/callback
   ```

5. Restart the backend. `GET /v1/integrations/google/status` now reports
   configured, and the app's Settings screen shows a **Connect Google** button.

### Why HTTPS is mandatory

Google permits an `http://` redirect URI **only** for `localhost`. A phone
cannot use the laptop's localhost, so connecting Google from the device
requires a real HTTPS URL — which is what `scripts/tunnel.ps1` provides. This
is not a preference; the flow cannot complete otherwise.

### Scopes, and the one that is deliberately missing

| Scope | What it allows |
|---|---|
| `calendar.events` | Create and read events. Cannot delete calendars or read settings. |
| `gmail.compose` | **Create drafts. Cannot send.** |
| `openid`, `email` | Show which account is connected. |

`gmail.send` is **never requested**. Approval authorises writing to your
calendar and drafting — the send stays your click, inside Gmail. If you add
`gmail.send`, you are making a product decision about irreversible side
effects; read the docstring in `app/services/google/gmail.py` first.

### Things that will bite you

- **Refresh tokens expire after 7 days** while the OAuth client is in
  "Testing" publishing status. That is Google policy, not a bug. The account
  is marked invalid and the app prompts a reconnect.
- **Google returns a refresh token only on first consent**, which is why
  `prompt=consent` is always sent. A token response without one is refused
  outright rather than stored — otherwise the connection works for an hour
  and then dies with no unattended way to recover.
- **OAuth will not run in a webview** (`disallowed_useragent`). The app opens
  the consent page in the system browser.

## Running for phone testing

```bash
pwsh ./scripts/serve.ps1 -Port 8010
```

Binds `0.0.0.0` (the phone cannot reach a backend bound to `127.0.0.1`),
prints the LAN address to type into the app, and sets `--proxy-headers` so
request logging stays correct behind a tunnel.

## A stable HTTPS URL

```bash
pwsh ./scripts/tunnel.ps1
```

Needs `cloudflared`:

```bash
winget install --id Cloudflare.cloudflared
```

The quick tunnel needs no Cloudflare account but its URL changes on every
restart — which means re-entering it in the app *and* re-registering the
redirect URI with Google each time. Once you are testing daily, use a named
tunnel on a domain you control:

```bash
pwsh ./scripts/tunnel.ps1 -Name audaxity -Hostname api.example.com
```

## Phase 11 endpoints

| Route | What it answers |
|---|---|
| `GET /v1/sessions/{id}/receipt` | What was captured, and what was not. Works at **every** status, `failed` included. |
| `GET /v1/tasks/open-loops` | What you owe / are owed, in four buckets. |
| `GET /v1/voice/status` | Whether a voiceprint exists, and whether matching can be trusted. |
| `POST /v1/voice/enrol` | Multipart: `files` (3+ clips) plus `durations` (comma-separated seconds). |
| `POST /v1/voice/check` | Returns a verdict for one clip - never an error, because "we don't know" is the honest answer. |
| `DELETE /v1/voice` | Deletes the voiceprint outright. Idempotent. |

### Voice enrolment: what is and is not switched on

Enrolment, verification, the three-way verdict and deletion are all built and
tested. **No speaker model is wired up**, so
`SpeakerEmbeddingDriver.trustworthy` is `False`, and `SpeakerMatch.is_owner`
returns `False` however high the similarity. Nothing can attribute a
commitment on an unvalidated model's word.

To wire a real one up, read `app/services/voice/factory.py` first - it records
the measurement sequence to follow and why a leaderboard pick would be
choosing in the dark. Give any new driver a **new `model_id`**: profiles store
the model that produced them and refuse cross-model comparison.

### Running it on an HTTPS URL (phase 8)

`cloudflared` is vendored at `tools/cloudflared.exe`, because winget's MSI
download is served from GitHub's release CDN and was reset repeatedly from
this network while everything else downloaded fine. `scripts/tunnel.ps1`
prefers that copy over one on PATH.

```powershell
./scripts/serve.ps1            # backend on 0.0.0.0:8010
./scripts/tunnel.ps1 -Port 8010  # prints an https://*.trycloudflare.com URL
```

The quick tunnel's URL changes on every restart, which means re-registering
the Google redirect URI each time. Use `-Name` with a hostname you control
once you are iterating daily.

**A quick tunnel is public.** Auth is on by default (`AUTH_REQUIRED=true`), so
the routes answer `401` without a bearer token - but do not leave one running
unattended.

## People endpoints (memory phase B)

| Route | What it does |
|---|---|
| `GET /v1/people` | Everyone seen, plus **suggested** duplicates |
| `PATCH /v1/people/{id}` | Rename, or mark as yourself (`is_self`) |
| `POST /v1/people/{id}/aliases` | Teach another spelling (409 if it belongs to someone else) |
| `POST /v1/people/{id}/merge` | Fold another person in; nothing is discarded |
| `DELETE /v1/people/{id}` | Delete the identity, not the conversations |

There is deliberately **no route that merges on the server's judgement**.
Automatic resolution covers exact keys, squashed keys (spacing removed and
repeated letters collapsed, so "Pull kit" → "Pulkit") and token containment.
Everything merely similar is a suggestion the user applies.

`is_self` is the single most useful thing a user can tell the backend: it
decides the own/owed split in the open-loops ledger for every past and future
task, and it is never inferred from a name.

## Running the eval set honestly

```bash
python scripts/run_eval.py --provider groq --delay 22     # all cases
python scripts/run_eval.py --provider groq --case media_bleed --delay 0
```

**One run is not a measurement.** Extraction now runs at `temperature=0.0`
with a fixed `EXTRACTION_SEED`, which took `media_bleed` from 1/3 to 5/5 —
but a mixture-of-experts model is not fully deterministic even so. Before
concluding that a prompt edit changed behaviour, run the affected case a few
times on both sides of the change.


## Google scopes: Calendar only by default

`GOOGLE_SCOPES` requests `calendar.events`, `openid` and `email`. **No Gmail
scope.**

Phase 10 originally requested `gmail.compose`, believing it could draft but
not send. It cannot be relied on for that: Google documents it as "Manage
drafts and send emails", and `users.drafts.send` and `users.messages.send`
both accept it. `gmail.send` is the *narrower* scope — sensitive, where
`compose` is **restricted** and needs restricted-scope verification plus a
security assessment to ship.

Set `GOOGLE_ENABLE_GMAIL=true` to add it and enable the draft executor. With
it off the executor is not built and `EMAIL_DRAFT` approvals fail loudly,
which is the pre-existing deliberate behaviour.

### Registering the OAuth client

1. **console.cloud.google.com** → create or pick a project.
2. **APIs & Services → Library** → enable **Google Calendar API**. Only
   enable the Gmail API if you are turning `GOOGLE_ENABLE_GMAIL` on.
3. **Google Auth Platform → Branding** → fill in the app name and your
   support email.
4. **Audience** → User type **External**, and add your own Google account
   under **Test users**.
5. **Clients → Create client** → *Web application*. Add the exact
   **Authorised redirect URI** from `GOOGLE_REDIRECT_URI` in `.env` — Google
   matches it character for character.
6. Copy the client id and secret into `backend/.env`, restart the backend.

**The 7-day gotcha.** While the app's publishing status is **Testing**,
Google issues refresh tokens that **expire after 7 days**, so you will be
asked to reconnect weekly. The `needs_reconnect` state handles that
gracefully, but it is not a bug to chase. Moving the status to **In
production** removes the expiry; with a sensitive scope that means going
through verification. For a personal app, weekly reconnects are usually the
cheaper trade.

**The tunnel gotcha.** A quick tunnel's hostname changes on every restart and
Google matches the redirect URI exactly, so each restart means re-registering
it. Use `./scripts/tunnel.ps1 -Name <name> -Hostname <host>` for a stable one
before registering anything.
