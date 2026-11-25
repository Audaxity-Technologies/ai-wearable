# Roadmap

What is built, what is broken, and what to build next — in the order I would
build it. Written 2026-11-25.

Three sources feed this: [ARCHITECTURE.md](ARCHITECTURE.md) for phase history,
[MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) for the retrieval plan, and
[FEATURE_PROPOSALS.md](FEATURE_PROPOSALS.md) for the competitive reasoning.

---

## Where we are

Phases 0–8 and 10–13 are done. 522 backend tests; the extraction eval set runs
9/9 cases at 33/33 on items, act, owner and date, with zero spurious
extractions.

Working end to end on a real phone: record → segmented upload → transcribe →
extract speech acts → resolve dates → write memory → propose actions →
approve → execute. Plus Ask with citations, a daily briefing, auth, push,
coarse place capture, person identity, the capture receipt and the open-loops
ledger.

---

## 0. Finish what is already half-built

**These are defects, not features.** Each one is something shipped this month
that does not yet do what its own documentation claims. They come first
because the alternative is a product whose promises are approximately true.

### 0a. Validate the speaker model — IN PROGRESS: tooling done, needs audio

Owner voice enrolment is built and **deliberately inert**: `trustworthy` is
`False`, so `SpeakerMatch.is_owner` can never return true, whatever similarity
it computes. No speaker model is wired up.

The work is not coding, it is measurement. Record 20–30 clips on the actual
device — you, other people, quiet room and noisy room — then measure TitaNet,
ECAPA-TDNN and ReDimNet2 on *that*, and set `OWNER_THRESHOLD` and
`OTHER_THRESHOLD` from the measured distributions rather than from a paper's.
Published 0.1–3% EER figures are all clean-corpus, and a 2026 comparison found
domain shift mattered more than the gaps between models.

**Why first:** it is the thing that makes the open-loops ledger useful rather
than merely honest. Today most loops land in `unattributed` because nothing
can tell who said "I'll send it".

**Built 2026-11-25:**

- `scripts/measure_speaker_models.py` — enrols on the first three owner clips
  exactly as the app does, holds the rest out, and reports EER, the two
  thresholds derived from the measured distributions, the width of the
  uncertain band, and the worst cases by filename so a bad recording can be
  listened to. Warns explicitly when the distributions overlap so badly that
  no conservative threshold pair exists.
- `scripts/collect_voice_clips.py` — lists recorded sessions and lays their
  stored segments out as labelled clips. Going through the app matters: it
  guarantees the same recorder settings and AAC encoding the product uses,
  which is the domain shift that makes published EER figures misleading.
  `--as owner|other` is required and never inferred.
- [VOICE_ENROLMENT_PROTOCOL.md](VOICE_ENROLMENT_PROTOCOL.md) — what to record,
  including the mistake that makes a bad model look good (recording every
  owner clip in one sitting) and the hardest impostor case (same gender,
  similar accent).
- 12 tests, written because the harness had a real bug: the two thresholds
  were derived from the wrong distributions, which on cleanly separated data
  produced an owner threshold hugging the impostor scores and no uncertain
  band at all. Running it caught that; reading it did not.

**Remaining: the audio.** 8+ owner clips across varied conditions and 8+
impostor clips. No model is wired up and none should be until a measured
verdict justifies it.

### 0b. Move media suppression out of the prompt — 3–4 days

Rule 13 was measured at **1 pass in 3** before extraction was pinned to
temperature 0, and 5/5 after. That is a real improvement and still a marginal
mechanism: one instruction competing with thirteen others for attention.

The durable version, as the proposal said when the rule was written, is an
audio classifier ahead of transcription — speech vs music vs broadcast on each
segment. It also **saves money**, because suppressed audio is never sent to
Groq at all.

### 0c. Repository conformance tests — 1 day

Write one test suite against the `Repository` Protocol and run it twice, once
per implementation. Today there is only one implementation, so this looks
pointless; it is what makes the Postgres migration a weekend instead of a
rewrite, and it is much cheaper to write now than to retrofit against two
implementations that already disagree.

---

## 1. The critical path: make memory answerable

This is the product thesis — "a database of my brain" — and it is the only
part no competitor can copy quickly, because it needs speech acts and
identity, which they do not store.

Phases A and B are done (place capture, person identity). What remains:

### 1a. Phase C — the utterance index — DONE 2026-11-25

One row per utterance: speaker, addressee, absolute timestamp, place, session,
speech act, and later an embedding.

**Why it matters:** retrieval today works on whole *sessions*. "What did Pulkit
and I decide that day" needs one sentence out of a two-hour recording, and a
session-grained index either returns the whole thing or scores it below a
session that merely says "Pulkit" five times. Place and speaker also change
mid-session, so the utterance is the only grain where they mean anything.

### 1b. Phase D — the question parser — DONE 2026-11-25

**The single highest-value piece of work left in this project.**

Classify the question, resolve its entity mentions to ids and its time
expressions to absolute ranges, build a structured filter, retrieve, rank,
answer with citations — and **ask when ambiguous** rather than guessing.
"Which Pulkit?" is a better answer than a confident wrong one.

`core/dates.py` already resolves time phrases in *speech*; this is the mirror
image for *questions*, plus anchoring ("that day" anchors to what was just
discussed, not to today).

### 1c. Phase G — the retrieval eval set — alongside D, not after

Question → expected-citation pairs, built as D is built.

**This is not optional.** The extraction eval caught two prompt regressions
and one wrong label this month, and retrieval has strictly more ways to
degrade silently. Borrow the question taxonomies from LongMemEval and LoCoMo
rather than their scores.

### 1d. Phase E — bi-temporal facts — 4–5 days

Give every fact `valid_from`/`valid_to` (true in the world) separately from
`observed_at` (when we heard it). `Memory.conflicts_with` can say two
statements disagree; it cannot say which was true in March.

This is what answers "when did the launch date change?" and "how many times
has this slipped?" — **a to-do list cannot tell you your launch date has moved
four times.**

### 1e. Phase F — embeddings, last and deliberately — 2–3 days

As a *ranker inside the structured filter*, never as the retrieval system.
Embeddings are specifically bad at negation, time and who-said-it: "Pulkit
promised to send the forms" and "I promised Pulkit I'd send the forms" are
near-identical vectors and opposite facts.

Last because they make a bad retrieval system *feel* better while making it
harder to debug — every answer becomes plausible.

---

## 2. Infrastructure, forced rather than chosen

### 2a. Don't transcribe silence (VAD) — 2–3 days

**The single biggest cost lever in the product.** Speech-to-text is ~94% of
variable cost ($2.40/user/month at 2h/day, against $0.15 for all the LLM
work). Ambient wear is mostly non-speech, so voice-activity detection before
upload plausibly removes 50–80% of audio seconds.

Also check the segment length against Groq's **10-second minimum billing** —
uploading 3-second chunks triples the cost of the same audio.

### 2b. Postgres + pgvector — 2–3 days, when forced

The JSON store fails at the **second process**, not the hundredth user: the
lock is in-process, so running the API on two workers risks silent data loss.

Migrate when any of these happens, whichever is first: you deploy anywhere
that isn't the laptop, a second person gets an account, the worker becomes its
own process, or embeddings land (phase F). One Postgres with `pgvector`, not a
separate vector database — retrieval here is filter-then-rank, which is one
SQL statement in Postgres and a two-system dance anywhere else.

### 2c. A second STT provider — 1 day

**Rate limits bite before cost does.** Groq's free tier is 7,200 audio
seconds/hour — one user recording continuously. Developer tier reaches roughly
55. The `TranscriptionDriver` Protocol already exists; add Deepgram or
Fireworks behind it *before* you need it, and make 429 a retry-with-backoff
rather than a failure.

---

## 3. Features worth building after the critical path

In the order I would do them.

| # | Feature | Why | Est. |
|---|---|---|---|
| 3a | **Price-check card** | Post-conversation "you discussed X at ₹Y; it's ₹Z elsewhere". Tests the deal-watch idea with no streaming rewrite. Only feature here with revenue attached. | 1 week |
| 3b | **Entity-scoped Ask** | "What has Priya committed to?", "what did we decide and then un-decide?" Needs C+D; the memory layer already preserves contradictions, which no competitor does. | 3 days |
| 3c | **Weekly review** | Decisions quietly reversed, commitments aging out, people not spoken to in a month. Reuses the briefing scheduler. | 2 days |
| 3d | **Agentic voice commands** | "Draft a reply to Pulkit saying I'll send the forms Monday." Phase 10 already built the approval-gated executor; what is missing is a command path distinct from ambient capture. Needs identity, which now exists. | 4–5 days |
| 3e | **Consent mode** | Chime on start, per-session "who was present", stricter default in all-party-consent jurisdictions (~11 US states; California SB 1130 would bar disabling the indicator). Cheap now, expensive to retrofit after a law lands. | 2–3 days |
| 3f | **MCP server** | Expose the user's own memory to Claude and ChatGPT. Small work, large reach, and it reframes Audaxity as the capture-and-judgement layer rather than another chat app. | 2–3 days |

---

## 4. Blocked, and what unblocks them

| Item | Blocked by |
|---|---|
| **Background recording, screen off** | CMake 4.5.1 installed 2026-11-25 to `%LOCALAPPDATA%\Android\Sdk\cmake\4.5.1`. The earlier diagnosis ("needs a newer CMake because ninja hits the 260-char path limit") was shaky: long paths were already enabled in the registry, the longest real path was 190 chars, and the 3.22.1 configure step had in fact **succeeded**. A build is now getting past CMake. |
| **Phase 9 — ESP32 band firmware** | Deferred by decision. The ingest API already accepts `source: "band"`, so this is a separate track whenever hardware starts. |
| **Shipping Gmail drafting** | `gmail.compose` is a *restricted* scope requiring verification plus a CASA security assessment. Off by default; revisit only if drafting proves valuable. |
| **A stable public URL** | Only needed for device-driven OAuth and real users. Not needed now: Google permits `http://localhost` redirects, so the connection is made once from the laptop and the backend holds the refresh token for every device. |

---

## 5. Explicitly not building

Unchanged from [FEATURE_PROPOSALS.md](FEATURE_PROPOSALS.md), and worth
restating because each is a thing competitors do:

- **Summary templates.** PLAUD ships "10,000+". That is an admission the
  product cannot tell what you need. A commitment looks like a commitment
  wherever it was said.
- **Always-on by default.** Bee's default is the source of most of its
  over-extraction and all of its privacy coverage. Deliberate capture is a
  product position — and after the Amazon and Meta acquisitions, a marketable
  one.
- **Identifying every speaker by voiceprint.** The capability is tempting and
  the liability is a biometric database of people who never consented.
- **Sending email.** A wrong calendar event costs a correction; a wrong sent
  email costs a relationship.

---

## The order, in one list

```
0a  speaker model: measure on real audio          3–4 d   ← unlocks the ledger
0c  repository conformance tests                    1 d   ← cheap insurance
1b  phase D: question parser  + 1c eval set       5–6 d   ← the product
2a  VAD: stop transcribing silence                2–3 d   ← 94% of the bill
1d  phase E: bi-temporal facts                    4–5 d   ← the differentiator
0b  media suppression → audio classifier          3–4 d
1e  phase F: embeddings as a ranker               2–3 d
3a  price-check card                                1 w   ← demand test
...then 2b/2c when forced, and tier 3 on evidence
```

Roughly six weeks of focused work to the point where "what did Pulkit and I
decide that day" is answerable with citations — which is the demo that makes
the rest of it obvious.
