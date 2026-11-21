# Audaxity Mobile (phase 7)

Expo + React Native app for the Audaxity backend. Record conversations,
review what the assistant understood, and approve or reject the actions it
proposes.

## Setup

```bash
npm install
```

```bash
npx expo start
```

Then open it with Expo Go, or press `a` / `i` for an emulator.

**Recording needs a development build, not Expo Go**, on a real device:
`expo-audio` records fine in Expo Go for quick trials, but background capture
and reliable long sessions want a dev client:

```bash
npx expo run:android
```

## Pointing the app at your backend

This is the step that trips everyone up. Your phone cannot reach
`localhost` — that resolves to the phone itself. In the app, open
**Settings → Server address** and enter your computer's address on the same
Wi-Fi network, for example `http://192.168.1.20:8010`.

Start the backend so it accepts connections from the network:

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Find your machine's LAN address with `ipconfig` (Windows) or
`ifconfig | grep inet` (macOS/Linux). The Settings screen has a **Test
connection** button that reports exactly what it found, so a typo is obvious
before you try to record.

> Port 8000 is often already taken by another service. The examples here use
> 8010.

## Screens

| Screen | What it is for |
|---|---|
| **Record** | Start/stop capture. Shows an unmissable recording state, the offline queue, and backend reachability. |
| **Talks** | Conversations grouped by day, with live pipeline status. Tap through for the summary, extracted items and transcript. |
| **Approvals** | The signature screen. Each proposed action shows its type, its date, *why* it was proposed, and the verbatim quote it came from. |
| **Tasks** | Open and completed commitments, with owner, deadline and the evidence behind each one. |
| **Ask** | Questions about your own history. Answers always carry their sources. |
| **Memory** | People, projects and facts, including contradictions between conversations. |
| **Settings** | Server address, segment length, identity, privacy notes. |

## How recording works

Audio is captured in fixed-length **segments** (45s by default), not one long
file. Each segment gets a client-generated UUID, is written to an on-device
SQLite outbox, and is uploaded opportunistically.

This matters because:

- **Recording never depends on the network.** You can record in a lift. The
  queue drains when the server is reachable again, and the Record screen tells
  you how many segments are waiting.
- **Retries are safe.** Ingest is idempotent on `segment_id`, so a re-sent
  segment returns `duplicate: true` and creates nothing.
- **Processing starts early.** The backend transcribes segment 1 while
  segment 5 is still being recorded.
- **The session is only closed once every segment has landed**, so the backend
  never analyses half a conversation.

## Privacy, as built

- The recording state is always visible while recording, and stopping is one
  tap.
- Deleting a conversation removes its audio, its transcript, and the memories
  and tasks only it supports.
- The app never stores transcripts of its own; everything lives on your
  backend.

Tell the people you are with that you are recording. In many jurisdictions
that is also a legal requirement.

## Accounts and notifications

Sign in with an email and password; everything is scoped to that account and
nothing is readable without a token. The old `user_id` text field is gone —
a client can no longer claim who it is.

**Notifications** register this install with the backend on first successful
connection. Three kinds arrive:

| Kind | What it does |
|---|---|
| `actions_ready` | A conversation finished; tap to open the approval inbox |
| `daily_briefing` | A morning summary: due today, overdue, needing a date |
| `wake_request` | Someone asked you to start recording |

**A wake-up call never starts the microphone.** Anything that can reach your
server can send one — a cron job, a calendar hook, another device:

```bash
curl -X POST http://<server>/v1/wake -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' -d '{"reason":"Standup starting"}'
```

It arrives on its own high-importance Android channel and opens the Record
screen with the reason shown. **Recording still begins only when you press the
button.** Starting capture from a remote trigger would be covert recording,
which this product does not do, and which Android 14+ blocks from the
background in any case. The notification is the wake-up call; your tap is the
consent.

**The morning briefing** is sent at a time you choose, in your own timezone.
The backend checks every minute and sends once per local day.

## Background recording

Capture continues when the screen locks. Two things make that work, and both
need a **dev build** — Expo Go cannot do background audio at all:

1. `app.json` configures the `expo-audio` plugin with
   `enableBackgroundRecording: true`. That registers
   `expo.modules.audio.service.AudioRecordingService` with
   `android:foregroundServiceType="microphone"` and adds
   `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`, `RECORD_AUDIO` and
   `POST_NOTIFICATIONS`.
2. `setAudioModeAsync({ allowsBackgroundRecording: true })` when recording
   starts, and `false` when it stops.

The foreground service posts a **persistent notification** for as long as
recording runs. That is not an inconvenience to design around — it is the
visible recording indicator the product requires, enforced by the OS. It
disappears when recording stops, because stopping releases the audio session
and tears the service down.

Build and install it:

```bash
npx expo run:android
```

Android 14+ forbids *starting* a microphone foreground service from the
background, which is why a wake-up call notifies rather than records. Once
recording has started in the foreground, it continues when the screen locks.

## Google Calendar (phase 10)

Settings has a **Google Calendar** card. Connecting it changes what approval
means:

| | Not connected | Connected |
|---|---|---|
| Approve a calendar event | Recorded in Audaxity | **Added to your Google Calendar** |
| Approve a dated reminder | Recorded in Audaxity | **All-day event on your calendar** |
| Approve an email draft | Refused, with an explanation | **Draft saved in Gmail** |

**It can never send email.** The backend does not request `gmail.send`, so
the permission does not exist to be misused. The card says so where the user
grants access, because that is the only place they can see it.

Two behaviours worth knowing:

- The consent page opens in the **system browser**, not a webview — Google
  rejects OAuth in embedded webviews, and a webview could read what you type
  into Google's login form. Come back to the app and press **Refresh** after
  signing in.
- Connecting from the phone **requires an HTTPS backend URL**, because Google
  only allows plain-HTTP redirects to `localhost`. Run
  `scripts/tunnel.ps1` and point the app at the tunnel URL.

## Not done yet
- **No live transcription view** while recording; the backend's SSE endpoint
  does not exist yet.
- **Band support is not here**, and deliberately does not need to be: the band
  will POST to the same `/v1/ingest/audio` endpoint this app already uses.
- Editing a proposed action is supported by the API (`PATCH /v1/actions/{id}`)
  but the app currently offers only approve and reject.

## Verified on a physical device (2026-11-19)

Run on an OPPO F27 Pro+ 5G via Expo Go over Wi-Fi, against a backend using
real Groq. A 45-second recording produced: microphone permission granted,
segment captured and queued, uploaded (732 KB of `audio/m4a`), transcribed by
Whisper, analysed into 6 items, 6 tasks and 6 proposed actions — each with its
verbatim quote and a resolved date.

Three bugs the device found that nothing else would have:

1. **`Unsupported FormDataPart implementation`.** React Native 0.88 rejects
   the classic `{uri, name, type}` FormData part. Uploads now use
   expo-file-system's native multipart (`File.upload`), which also streams
   from disk instead of loading the audio into JS memory.
2. **Segment rotation ran inside a `setState` updater**, making the segment
   boundary a side effect of rendering — React is free to run that twice or
   defer it. It now advances a ref and rotates outside the updater.
3. **A session stopped while uploads were still queued never closed.**
   Refusing to close early is correct (the backend must not analyse half a
   conversation), but nothing closed it once the queue drained. The outbox now
   closes the session when the last segment lands.

The offline queue proved itself incidentally: segments recorded while uploads
were broken sat in the outbox and flushed intact once the code was fixed.

Still unverified: background recording with the screen off, and sessions
longer than a couple of minutes.

## Phase 11 screens

| Where | What |
|---|---|
| Conversation detail | **Capture receipt**, above the summary. Whether to believe the analysis comes before reading it. |
| Home → Open loops | What you owe / are owed, with the quote each came from. |
| Settings | **Your voice** - enrol, check, delete. |

Three wording rules in these screens are load-bearing, not cosmetic:

1. **The receipt's headline sentence comes from the backend.** It is not
   composed on the client. Two implementations of "did we capture this" would
   eventually disagree, and the one the user reads would be the wrong one.
2. **"Not attributed" is a bucket, never a default.** An unowned commitment is
   not shown under "You owe". Telling someone they promised something they
   never said is the costliest error this product can make.
3. **"Gone quiet" describes our records, not a person.** It means we heard it
   once, a while ago, and nothing since - not that anyone forgot.

The voice card also has to say plainly that matching is **not in use yet**
(`verification_trustworthy: false`). An "Enrolled ✓" badge over a feature that
cannot act would be exactly the dishonesty the rest of the app avoids.

## Remembering places (memory phase A)

Off by default. Settings → **Remember places**.

Each recording gets a place rounded to ~110m, captured **once per segment**
while recording and never carried over to the next one. Full precision never
leaves the phone, and the server rounds again so the guarantee does not depend
on this app being correct.

Only `ACCESS_COARSE_LOCATION` is declared, and `Accuracy.Balanced` (~100m) is
requested rather than `High`: asking for GPS precision we then discard would
cost battery and request more than the feature uses.

**Testing it:** `expo-location` is bundled in Expo Go, so this works there
today. Note that Expo Go uses its own manifest, so the permission strings and
the coarse-only declaration in `app.json` only take effect in a dev or
production build.

**The irreversible bit, stated on the toggle too:** turning this on later does
not fill in past recordings. Everything else in the memory plan can be rebuilt
from stored audio; where you were cannot.

## People (memory phase B)

Home → **People**. Two jobs:

1. **Fix duplicates.** Clear mis-hearings are joined automatically; the rest
   appear as suggestions with a *reason* that can argue against itself — two
   similar names that appear in the same conversation are usually two people,
   and the card says so rather than showing a bare score. Nothing merges
   without a tap.
2. **Say which one is you.** One tap fixes "you owe" vs "owed to you" across
   every task already recorded. The screen prompts for it while nobody is
   marked, because until then commitments stay under "not attributed" rather
   than being guessed at.

Aliases are shown per person ("also heard as: pull kit") — that is how you can
tell a mis-hearing was absorbed onto the right person rather than silently
attached to the wrong one.
