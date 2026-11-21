# Audaxity — System Architecture (v0.1)

Status: proposed, not implemented. Audience: Pulkit (CEO), Manan (CTO).
Date: 2026-11-19.

---

## 0. The one decision that shapes everything else

**The band is not special. It is one of three clients of the same ingest API.**

```
                 ┌─────────────────────────────────────────┐
  Clients        │  1. Phone mic  (build this FIRST)       │
  (interchange-  │  2. File upload / web  (for testing)    │
  able)          │  3. ESP32-S3 band via phone or Wi-Fi    │
                 └──────────────────┬──────────────────────┘
                                    │  POST /v1/ingest/audio
                                    ▼
                          ┌───────────────────┐
                          │  FastAPI gateway  │  auth, validation, dedupe
                          └─────────┬─────────┘
                                    │ enqueue job
                                    ▼
                    ┌───────────────────────────────┐
                    │  Pipeline worker (in-process) │
                    │  transcribe → analyze →       │
                    │  memory → tasks → actions     │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  Storage (repository layer)   │
                    │  JSON now → SQL later         │
                    └───────────────┬───────────────┘
                                    │  GET /v1/... + SSE status
                                    ▼
                    ┌───────────────────────────────┐
                    │  React Native (Expo) app      │
                    │  record · review · approve    │
                    └───────────────────────────────┘
```

Why this matters: you can ship a **complete, demoable product on the phone's own
microphone in week 1–2**, validate the AI brain with real conversations, and the
day the ESP32 firmware works it points at the same endpoint and nothing in the
backend changes. Hardware stops being on the critical path.

Every reference product works this way — Omi, Bee, Forth and Plaud are all
"device → phone → cloud → app", and Forth is literally shipping its agent (Cue)
on the phone **before** the band ships. Copy that sequencing.

---

## 1. End-to-end flow, cloud to user

### 1.1 Capture
- User taps **Record** in the app (or squeezes the band).
- Audio is captured in **30–60 s segments**, not one long file. Reasons: bounded
  memory on the ESP32, resumable upload on flaky networks, and the pipeline can
  start transcribing segment 1 while segment 5 is still being recorded.
- Each segment gets a **client-generated UUIDv4** and a monotonic `seq`. The
  client also sends `started_at` (ISO-8601 with offset) and `tz`
  (e.g. `Asia/Kolkata`). The backend never guesses the user's timezone.
- Segments are written to an on-device outbox queue (SQLite on the phone) and
  uploaded opportunistically. Offline-first is not optional — Omi advertises
  offline record-and-sync as a headline feature.

### 1.2 Ingest
- `POST /v1/ingest/audio` — multipart: `file` + JSON metadata.
- Gateway validates auth, mime (`audio/wav|m4a|mp3|ogg`), duration, and a size
  cap of 20 MB per segment (stays under Groq's 25 MB request limit).
- **Idempotent**: if `segment_id` already exists, return `200` with the existing
  record instead of re-processing. This is what makes client retries safe.
- Audio bytes go to blob storage, metadata to the `recordings` store, and a job
  is pushed onto the pipeline queue. Returns `202 Accepted` with `recording_id`.
- A `session` groups segments (one conversation = one session). A session closes
  on explicit stop, or after 90 s with no new segments.

### 1.3 Pipeline (async, staged, resumable)
Each session walks a status machine. Status is persisted after every stage, so a
crash resumes instead of restarting:

```
queued → transcribing → transcribed → analyzing → analyzed
       → memory_written → actions_proposed → ready
                                            ↘ failed(stage, reason, retry_count)
```

**Stage 1 — Transcription.** Segment audio → text with word/segment timestamps
and detected language. Stored per segment, concatenated per session.

**Stage 2 — Conversation analysis.** One LLM call per session over the full
transcript with a *strict* output schema (see §4). Produces participants,
topics, projects, decisions, tasks, commitments, deadlines and a summary — each
with a `source_quote` and a `confidence`.

**Stage 3 — Memory write.** Entities (people, projects, facts) are resolved
against existing memory, then created, updated, or flagged as conflicting.
Never silently overwritten.

**Stage 4 — Task sync.** Extracted commitments become tasks with owner, due
date, and a link back to the source session and quote.

**Stage 5 — Action proposal.** Tasks and decisions become *proposed* actions
(reminder, calendar event, email draft, plain task), each carrying its reason
and provenance. Nothing executes here.

### 1.4 Delivery to the app
- The app holds a list of sessions and polls
  `GET /v1/sessions?updated_since=` on foreground, plus **SSE** on
  `GET /v1/sessions/{id}/events` while a session is live, so the user watches the
  transcript and the action cards appear.
- Push notification ("3 actions need your review") when a session hits `ready`.
  Expo Notifications; defer until the core loop works.

### 1.5 Approval and execution
- `POST /v1/actions/{id}/approve | reject`, `PATCH /v1/actions/{id}` to edit.
- Approval is the **only** thing that moves an action to `executing`. Execution
  carries an idempotency key, so a double-tap or a retry cannot send two emails.
- Phase 1 "execution" means writing to our own store and firing a local
  notification. Google Calendar and Gmail arrive later behind the same interface.

---

## 2. Technology choices, and where I disagree with the brief

| Concern | Brief says | Recommendation | Why |
|---|---|---|---|
| Backend | FastAPI + Pydantic | **Agreed** | Async, typed, fast to build. |
| STT | Faster-Whisper (local) | **Groq `whisper-large-v3-turbo` API, with a local Faster-Whisper driver behind the same interface** | The important change. Faster-Whisper wants ~2–4 GB RAM and real CPU; no free cloud tier (Render/Fly, 256–512 MB) runs it, so "local Whisper" means the backend can only ever live on your laptop — and the phone can't reach your laptop. Groq's free tier is 20 RPM / 2 000 req-day / 7 200 audio-seconds-hour, faster than realtime, same API shape as OpenAI's. Keep the local driver for offline dev and as a cost escape hatch. |
| LLM | Gemini | **Provider interface; Groq (Llama) primary, Gemini secondary** | You said "grok or any free tier" — I read that as **Groq**, which is the better phase-1 pick because one key covers both STT and LLM. Gemini's free daily quota is reported inconsistently across sources (20 → 1 500 RPD depending on model and date), which is exactly why neither provider should be hard-coded. |
| Storage | Local JSON files | **JSON behind a repository interface — agreed for phase 1, with a defined migration trigger** | Honest flag: JSON files are not safe for concurrent multi-user writes, and a cloud backend serving a phone *is* multi-user. Phase 1 mitigations: one worker process, one `asyncio.Lock` per collection, atomic writes (temp file → `os.replace`). That genuinely holds for you two plus a handful of testers. **Migration trigger: more than 3 concurrent users, more than ~2 000 sessions, or the first time you want full-text search.** Then swap the repo implementation. The brief bans SQLite in phase 1 and I'll honour that — just know you're buying speed now and paying about a day of migration later. |
| Blob storage | — | Local disk volume now, **Cloudflare R2** (10 GB free, no egress fees) at deploy | Audio is the one thing that will actually cost money. |
| Queue | — | `asyncio.Queue` plus a persisted job record | No Redis. The persisted record gives crash recovery; the in-memory queue is just the scheduler. |
| Retrieval | Keyword/metadata | **Agreed** | Under ~1 000 sessions, metadata filters plus keyword scoring beat a vector DB and are debuggable. Revisit when "what did we decide about X" starts missing obvious hits. |
| Mobile | React Native + Expo | **Agreed** | Expo with a dev client (not Expo Go), because you need native audio and background recording. |
| Hosting (dev) | — | **Cloudflare Tunnel** to your laptop | Free, gives the phone a stable HTTPS URL in one command, no deploy step. Use it for the first two weeks. |
| Hosting (demo) | — | **Fly.io or Render**, one machine plus a persistent volume | Only once the JSON→SQL swap is done, or audio has moved to R2. |

---

## 3. Backend module map

```
backend/
  app/
    main.py                 FastAPI app, lifespan, router mounting
    config.py               pydantic-settings, .env
    api/v1/
      ingest.py             audio upload, session open/close
      sessions.py           list/get/transcript/SSE
      tasks.py              CRUD, pending, overdue
      memory.py             browse, search, delete
      actions.py            pending, approve/reject/edit
      ask.py                question answering
      devices.py            band registration, heartbeat
      health.py
    services/               <-- no FastAPI imports in here, ever
      transcription/        base.py, groq_driver.py, local_whisper_driver.py
      llm/                  base.py, groq_driver.py, gemini_driver.py, prompts/
      analysis.py           transcript -> ConversationAnalysis
      memory.py             entity resolution, dedupe, conflicts
      tasks.py
      actions.py            proposal generation
      approval.py           state machine + idempotent execution
      retrieval.py          keyword + metadata search for Ask
      pipeline.py           the stage orchestrator
    repositories/
      base.py               Protocol per collection
      json_repo.py          atomic write, per-collection lock
      blob.py               local disk / R2
    models/                 Pydantic: domain and API DTOs, kept separate
    core/                   logging, errors, auth, time/timezone utils
  tests/
```

Hard rule from the brief, worth restating: **services never import FastAPI, and
routes never contain logic.** A route validates, calls a service, maps to a DTO.
That is what makes the pipeline testable without HTTP, and lets the worker call
the same code path as a request.

---

## 4. The AI layer — where this product is actually won or lost

Transcription is a commodity. The differentiator is interpretation, and the
failure modes are specific.

**Schema-first extraction.** The analysis call returns JSON validated against a
Pydantic model. On validation failure: one repair attempt with the validator
error appended, then mark the session `analyzed_degraded` and keep the
transcript. Never crash the pipeline on a bad LLM response, and never show the
user a half-parsed guess.

**Speech-act classification is a required field, not a nuance.** Every extracted
item carries:

- `act`: `commitment | assignment | suggestion | decision | requirement | question | discussion`
- `owner`: a speaker label, a named person, `unknown`, or `group` — **`unknown` is a valid, expected answer**
- `source_quote`: a verbatim span from the transcript
- `confidence`: 0–1
- `due`: `{raw: "Friday", resolved: "2026-11-21", resolution: "confident|ambiguous|none"}`

"I'll send it" (commitment, owner = speaker), "you should send it" (assignment,
owner = addressee), "maybe we should send it" (suggestion, owner = unknown) and
"we need to send it" (requirement, owner = group) must land in different
buckets. The prompt states these four cases with examples, and the eval set
(§7) tests them.

**Relative dates are resolved in code, not by the LLM.** The LLM returns the raw
phrase; a deterministic resolver converts it using the session's `started_at`
and `tz`. LLMs are unreliable at date arithmetic, and this is the most
user-visible class of error. Where the resolver can't decide ("next week",
"soon"), the action is created with `needs_clarification` and the app asks.

**Nothing invented.** Anything without a `source_quote` is dropped before it
reaches storage. That one rule kills most hallucinated tasks and gives you "why
did you create this?" for free — the app shows the quote on every card.

**Memory is selective and provenance-tracked.** A candidate fact is stored only
if it is durable (a project state, a preference, a relationship) rather than
transient chatter. Every memory row keeps `source_session_ids[]`. A re-mention
bumps `confidence` and `last_seen`; a contradiction creates a second row linked
as `conflicts_with` rather than an overwrite. This matches Forth's model — key
facts with short quotes as evidence.

**Ask (QA).** Retrieve → ground → answer. Keyword and metadata search over
sessions, memories and tasks; top-k passages into the prompt; the answer must
cite `session_id`s and must say "I don't have that" when retrieval comes back
empty. No free-floating generation over the whole corpus.

---

## 5. API surface (v1)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/auth/register`, `/v1/auth/login` | user JWT |
| POST | `/v1/sessions` | open a recording session |
| POST | `/v1/ingest/audio` | upload one segment (idempotent) |
| POST | `/v1/sessions/{id}/close` | finalize, trigger analysis |
| GET | `/v1/sessions` | list, `?updated_since=&status=` |
| GET | `/v1/sessions/{id}` | summary, participants, projects, decisions |
| GET | `/v1/sessions/{id}/transcript` | segments with timestamps |
| GET | `/v1/sessions/{id}/events` | SSE: status and live transcript |
| DELETE | `/v1/sessions/{id}` | cascade delete audio, transcript, derived data |
| GET | `/v1/tasks` | `?status=pending\|overdue\|done&owner=` |
| PATCH | `/v1/tasks/{id}` | edit, complete, reassign |
| GET | `/v1/memory` | browse by entity type |
| GET | `/v1/memory/search` | keyword search |
| DELETE | `/v1/memory/{id}` | user correction or removal |
| GET | `/v1/actions/pending` | the approval inbox |
| POST | `/v1/actions/{id}/approve` | execute (idempotent) |
| POST | `/v1/actions/{id}/reject` | with an optional reason |
| PATCH | `/v1/actions/{id}` | edit before approving |
| POST | `/v1/ask` | grounded question answering |
| POST | `/v1/devices/register` | band pairing, returns a device token |
| POST | `/v1/devices/{id}/heartbeat` | battery, firmware, last-seen |
| GET | `/v1/health` | liveness and driver status |

Auth: a user JWT for app calls, and a separate long-lived **device token** for
the band, scoped to ingest and heartbeat only. A stolen band token cannot read
transcripts.

---

## 6. Mobile app

Expo + React Native, TypeScript, expo-router. State: TanStack Query for server
state (it handles polling, caching and retries) plus Zustand for recording UI
state. Local SQLite (`expo-sqlite`) for the upload outbox.

Screens, in build order:

1. **Record** — big record button, live elapsed time, segment-upload indicator,
   an unmistakable "RECORDING" state. Offline banner when the outbox is
   non-empty.
2. **Conversations** — list by day; each row shows title, duration, pipeline
   status and action count. Detail view: summary · transcript · tasks · people,
   with every extracted item tappable to reveal its source quote.
3. **Approvals** — the product's signature screen. Action cards showing type,
   title, resolved date, the *reason*, and the quote. Approve / Edit / Reject /
   Ask-to-clarify. This is the screen to demo.
4. **Tasks** — pending, overdue, by owner. Completion writes back.
5. **Ask** — chat over your own history, with source chips that link to sessions.
6. **Memory** — people, projects, facts, each deletable.
7. **Device & Privacy** — band status, battery, retention setting, delete-all.

Consent and visibility are UI requirements, not settings: an always-visible
recording state, a one-tap pause, and later the band's LED mirroring it.

---

## 7. Build order

Each phase ends in something demoable. Don't start the next one until the
previous has a test.

| Phase | Deliverable | Rough effort | Status |
|---|---|---|---|
| 0 | Repo, FastAPI skeleton, config, logging, JSON repo with atomic write, `/health`, CI running pytest | 1 day | **done** |
| 1 | Ingest, session model, blob storage, status machine. Upload a WAV with curl and see it stored | 1 day | **done** |
| 2 | Transcription service, Groq driver, local driver, segment timestamps | 1 day | **done** |
| 3 | **Analysis**: schema, prompts, speech-act classification, date resolver, repair loop. Build the eval set here | 2–3 days | **done** |
| 4 | Memory: entity resolution, dedupe, conflicts, provenance | 2 days | **done** |
| 5 | Tasks, action proposal, approval state machine with idempotency | 2 days | **done** |
| 6 | Ask: retrieval and grounded answers with citations | 1–2 days | **done** |
| 7 | Expo app: Record → Conversations → Approvals (phone mic only) | 4–5 days | **done** — verified on an OPPO F27 Pro+ 5G via Expo Go, 2026-11-19 and 2026-11-21 |
| 8 | Cloudflare Tunnel, auth, push notifications, end-to-end on a real phone | 2 days | **done** — auth, push and the end-to-end loop verified on a real phone. Cloudflare Tunnel verified 2026-11-21: `cloudflared` is vendored at `tools/cloudflared.exe` and the backend answered over `https://*.trycloudflare.com`. **Still unverified: background recording with the screen off** — needs a native dev build, blocked on a CMake newer than 3.22.1 (ninja 1.10.2 hits the Windows 260-char path limit) |
| 9 | ESP32-S3 firmware → the same ingest endpoint over Wi-Fi | separate track | **skipped for now** (band deferred by decision, 2026-11-21) |
| 10 | Google Calendar and Gmail execution drivers | after validation | **done** — OAuth, calendar events (idempotent via client event id), all-day reminders, Gmail **drafts only**. Not yet exercised against a live Google project: needs an OAuth client and an HTTPS redirect URI. |
| 11 | **Tier 1 differentiators**: media suppression, capture-integrity receipt, open-loops ledger, owner voice enrolment | 1 week | **done** except the speaker model — see below |
| 12 | **Memory phase A**: coarse place capture (~110m, opt-in, rounded twice) | 0.5 day | **done** — capture only; retrieval over it is phase D |
| 13 | **Memory phase B**: `Person` identity, aliases, merge, `is_self`, pronoun rule | 1 day | **done** |

### Phase 10 as built, and the two decisions inside it

**Calendar writes are genuinely idempotent.** Calendar's `events.insert`
accepts a client-supplied event id, so the id is derived from the action's
idempotency key (SHA-256 hex, which is base32hex-legal by construction). A
retried approval inserts the same id, Google answers 409, and the executor
treats that as success. "Did my approval go through?" became a fact rather
than a guess, with no side table of what we have sent.

**Gmail is not requested at all — a correction that removed a guarantee.**

Phase 10 requested `gmail.compose` rather than `gmail.send`, on the reasoning
that compose could draft but not send, making the backend "structurally
incapable" of sending mail. That claim went into the code, this document and
the app's own UI. **It was false.** Google's reference describes
`gmail.compose` as "Manage drafts and send emails", and both send methods
accept it:

```
users.drafts.send    one of: mail.google.com | gmail.modify | gmail.compose
users.messages.send  one of: mail.google.com | gmail.modify | gmail.compose
                             | gmail.send
```

`gmail.send` is in fact the *narrower* scope — Google classifies it as
sensitive, while `compose` is **restricted** and would require restricted-scope
verification plus a security assessment before shipping to anyone. There is no
draft-only Gmail scope, so the guarantee as designed was never available.

The scope is therefore not requested. Calendar only by default, which makes
"cannot send email" true for a better reason: there is no token that could.
Drafting is opt-in via `GOOGLE_ENABLE_GMAIL`, the executor is not built when
it is off, and with it on the UI states the weaker truth — the permission
allows sending, this code never does, and that is a property of the code
rather than of the permission.

The test that should have caught this instead encoded it. It was called
`test_the_requested_scopes_cannot_send_mail` and asserted that `compose` was
in the scope list and `send` was not. It passed throughout, because it
checked the scope list against the author's belief rather than against what
the scope permits. **A test that restates an assumption cannot falsify it** —
the replacement asserts no mail scope is requested, and a second test pins
the constant's name to what the scope actually does.

One asymmetry worth knowing about, in `GoogleOrLocalExecutor`: with **no**
Google account connected, execution falls back to local recording, because
the user never asked for a calendar write. With an account connected but its
authorisation stale, it **fails loudly** instead of falling back — a card
reading "recorded" when nothing reached the calendar is a lie the user would
only discover by missing the meeting.

**The tunnel is now a phase 10 dependency, not a convenience.** Google will
not accept an `http://` redirect URI for any host other than `localhost`, so
without an HTTPS URL the Google connection cannot be completed from the
phone at all. `scripts/tunnel.ps1` sets this up. `cloudflared` is vendored at
`tools/cloudflared.exe` (55MB — exclude it if this ever becomes a git
repository); the script prefers it over one on PATH, because winget's
MSI download is served from GitHub's release CDN and was reset
repeatedly from this network while every other download succeeded.

### Extraction is run at temperature 0 with a fixed seed, and why that was found late

Rule 13 (media suppression) was reported as passing after one live run. It was
not reliable. Running the single case repeatedly, after an unrelated prompt
change appeared to break it:

```
before   1 / 3 passes, three DIFFERENT failure modes
         - TV commentary extracted as a `requirement`  <- the exact failure
           rule 13 exists to prevent
         - the span not suppressed at all
         - a real commitment next to it missed entirely
```

So the earlier "8/8" was partly luck, and a single eval run is not a
measurement. Extraction ran at `temperature=0.1` with no seed; for a
structured task, sampling diversity buys nothing and costs reproducibility.
Now `temperature=0.0` with a fixed `EXTRACTION_SEED`:

```
after    5 / 5 passes
```

Two honest caveats. **5/5 is not proof of determinism** - it is five samples.
And a mixture-of-experts model is not fully deterministic even at temperature
0, because expert routing varies with how the provider batches requests, so
this reduces variance rather than removing it.

The durable lessons, both of which cost real time here:

1. **A flaky eval reports a prompt change as a regression.** The investigation
   began by suspecting a rule about pronouns had broken a rule about
   televisions, which was never plausible. Pin the sampling before trusting
   any comparison between runs.
2. **Same transcript, same items is a product property, not a test
   convenience.** A user asking "why did this change?" deserves an answer
   other than "sampling".

Rule 13 being this marginal also confirms what
[FEATURE_PROPOSALS.md](FEATURE_PROPOSALS.md) said when proposing it: the
prompt version is the cheap version, and the real one is an audio classifier
ahead of transcription. A single instruction competing with thirteen others
for attention in a long prompt is not where this belongs permanently.

### Memory phase B: identity, and a rule that was wrong

The automatic match rule written to fix "Pulkit" heard as "Pull kit" did not
fix it — the mis-hearing doubled the L too, so despacing alone left
"pullkit". A test caught that; `squash()` now collapses repeated letters as
well. The full account, including why token containment is automatic and why
`is_self` replaced display-name matching, is in
[MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md#phase-b-as-built-2026-11-21).

### Memory phase A: why place shipped before anything reads it

Everything else in the memory plan can be rebuilt by re-running a pipeline
over stored audio. **Location cannot** — there is no recovering where someone
was in March 2026 if nobody wrote it down in March 2026. So it ships ahead of
the retrieval that needs it. Full reasoning and the three refusals that
define it (rounded twice, one fix per segment, never inherited) are in
[MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md#phase-a-as-built-2026-11-21).

### Phase 11 as built: four features, and the one that is deliberately inert

Built from [FEATURE_PROPOSALS.md](FEATURE_PROPOSALS.md) Tier 1, after
cataloguing what the competition ships.

**1. Media and bystander suppression** (prompt rule 13). The most-cited
failure in this market: Forbes on Bee, "dozens of to-dos a day, many of which
are teased out of entertainment content that Bee has mistaken for things I
said". The rule tests the *speaker's presence*, never the topic - an advert's
"Call now" is a perfect assignment belonging to nobody - and it resolves its
uncertain case **toward extraction**, because a rule that silences speech it
cannot classify will eventually silence the user. Suppressed spans become
`DroppedItem`s with reason `media`, so over-suppression shows up on the
receipt instead of being silent. `evals/cases/media_bleed.json` asserts both
directions, and the second one matters more: "Remind me to watch the final
tomorrow" is about television and must still be extracted.

**2. Capture-integrity receipt** (`GET /v1/sessions/{id}/receipt`). The
complaint that makes people stop wearing these things is not a bad summary,
it is not trusting it. The receipt separates three things a progress bar would
flatten: **captured** (held and heard), **gap** (wall-clock time no audio
covers - our failure) and **silent** (held, nothing said - a quiet room). A
gap and a silence look identical on a timeline and mean opposite things.
Failed segments are deliberately **not** counted as coverage: we hold the
audio but cannot say what is in it. Available at every status including
`failed`, because a receipt you can only fetch once everything worked cannot
answer the question it exists for.

**3. Open-loops ledger** (`GET /v1/tasks/open-loops`). What you owe, what you
are owed, since when, from which quote, and whether it has come up again.
Impossible for competitors, who store flat to-do lists rather than speech
acts and owners. Four buckets, and the fourth is the design: an unattributed
commitment is **never** silently filed against the user, because that would
invent an obligation - the error prompt rule 2 exists to prevent. Sorted so
that a three-week-old undated promise outranks tomorrow's dated task: the
user can see tomorrow coming, and the old promise is what they actually
forgot.

**4. Owner voice enrolment** - built, and **intentionally unable to act**.

One voiceprint per account: the user's own, never anyone else's. Enrolling
every speaker would be a biometric database of people who never consented to
a device they did not buy; Forth designed exactly that and refused to ship
it. Verification has **three** outcomes - owner / other / **uncertain** -
because a binary decision would be a guess dressed as a fact, and
`"unknown"` is already a first-class honest answer everywhere else.

No speaker model is wired up, and that is the honest state rather than an
oversight. The published error rates (0.1-3% EER for TitaNet, ECAPA-TDNN,
ReDimNet2, pyannote) are all measured on clean corpora, and an independent
2026 comparison found domain shift mattered more than the gaps between them -
while wearable audio is the worst case for domain shift. So the driver sits
behind a Protocol with `trustworthy = False`, and `SpeakerMatch.is_owner`
consults it: **an unvalidated model cannot attribute a commitment to anybody,
whatever similarity it computes.** The app says so on the card. The sequence
is: measure candidates on real recordings from the actual device, set the
thresholds from those distributions, then add the driver with a new
`model_id` (which invalidates existing profiles rather than silently
comparing across models).

Building the ledger before the model was deliberate: it makes the value of
enrolment measurable rather than assumed, because today most loops land in
`unattributed` and the user can see exactly how many.

Live eval after rules 13 and 14, at temperature 0 with a fixed seed:
**9/9 cases, 33/33 on items, act, owner and date, 0 spurious.** The
earlier 8/8 figure was taken from a single run and was partly luck - see
the note on determinism above.

**The eval set in phase 3 is the highest-leverage item in this plan.** Record 10
real conversations between the two of you, hand-label the expected tasks,
owners, speech acts and dates, and re-run them on every prompt change. Without
it you will "improve" the prompt and silently regress owner attribution, and
you won't find out until a user complains that the AI assigned their commitment
to someone else.

---

## 8. Privacy and safety, built in

- Recording is deliberate and visibly indicated: a physical pause on the band, a
  software pause in the app.
- **Audio retention is a user setting**, default 7 days, after which the audio
  file is deleted and the transcript kept. (Bee deletes audio immediately; Forth
  deletes audio *and* transcript once the summary is written. Both are
  defensible — decide yours before launch, because it's a marketing claim as
  much as an engineering one.)
- `DELETE /v1/sessions/{id}` cascades to audio, transcript, memories derived
  solely from that session, and its proposed actions.
- Transcript text is never written to application logs. Log IDs and stage
  transitions only.
- TLS everywhere, device tokens scoped, and no consequential action without
  explicit per-action approval.
- India's DPDP Act 2023 applies to you, and two-party consent rules vary by
  jurisdiction. This needs a real legal read before any public launch — not
  before the prototype.

---

## 9. Technical debt accepted on purpose

1. JSON storage is single-process only. Migration trigger defined in §2.
2. In-process worker: a long transcription blocks nothing (it's async I/O to
   Groq), but a CPU-bound local Whisper run would. Acceptable while Groq is
   primary.
3. No vector search. Fine until the corpus grows.
4. Keyword retrieval will miss paraphrases ("the pitch" vs "investor deck").
   Mitigate with project alias lists before reaching for embeddings.
5. BLE audio streaming from the ESP32 is unproven and should be treated as a
   research task, not a planned feature. Wi-Fi HTTP upload, or SD-card-then-sync,
   for prototype 1 — exactly as the brief says.
