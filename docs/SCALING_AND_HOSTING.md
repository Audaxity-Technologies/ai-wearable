# Hosting, LLM and database at scale

Drafted 2026-11-21. Answers three questions: where this runs, what the AI
costs as users arrive, and whether the current database survives them.

Short version of all three:

1. **The database will not survive users.** It breaks at the *second process*,
   not the hundredth user. The migration is contained, though — one layer.
2. **Speech-to-text is ~94% of your variable cost. The LLM is a rounding
   error.** Optimise audio, not prompts.
3. **Rate limits bite before cost does**, at roughly dozens of users.

---

## 1. The database

### What we use now

JSON files, one per collection, behind the `Repository[ModelT]` Protocol in
`app/repositories/base.py`. `JsonRepository` loads the whole file, mutates a
dict, writes it back, guarded by an in-process lock.

This was the right call. It made phases 0–10 fast, it is trivially
inspectable, and the Protocol kept every service ignorant of it.

### Where it breaks — and it is earlier than "lots of users"

In order of how soon you hit it:

| # | Trigger | What happens | How close |
|---|---|---|---|
| 1 | **A second OS process** touching the data | in-process lock is useless; last writer wins; **silent data loss** | the moment you split the worker from the API, or run 2 uvicorn workers |
| 2 | Any collection past ~10k records | every `list()` is a full parse + O(n) scan | a few months of one user's segments |
| 3 | Multi-step writes | no transactions; a crash between "task created" and "action created" leaves inconsistency | already possible |
| 4 | >3 concurrent users | lock contention serialises everything | first handful of users |
| 5 | Embeddings | no vector index; brute force over every row | when memory phase F lands |

**Row 1 is the one to internalise.** The JSON store doesn't fail at scale, it
fails at *concurrency*, and the most ordinary deployment step there is —
running the API on two workers — triggers it. Today you are safe only because
everything is one process on your laptop.

### Where it goes: Postgres. One database, not two.

**Postgres + `pgvector` in the same instance.** Not a separate vector DB.

The reasoning, and it is not just simplicity: your retrieval is
**filter-first** (see [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md)) —
narrow by person, time and speech act, *then* rank. That is a `WHERE` clause
followed by a similarity ranking, which is one SQL statement in Postgres and a
two-system dance with a dedicated vector store, where the filter lives in one
database and the vectors in another and you join them in Python. The
architecture that is awkward for Pinecone is native for pgvector.

Published thresholds put pgvector comfortably ahead for anything under ~10M
vectors. One user generates on the order of 10⁴–10⁵ utterance embeddings a
year. You are three to four orders of magnitude from the crossover — the
sources are soft, but no reading of them puts you near it.

### What to do about it, and when

**Not now.** Migrating before you have users costs you the velocity that is
currently your only advantage. But do these two cheap things now:

1. **Freeze the `Repository` Protocol** and write a **conformance test suite**
   against it — one set of tests, run twice, once per implementation. Then the
   Postgres repository is a weekend, not a rewrite, and you'll find out at
   *write* time rather than in production that `list(**filters)` semantics
   differ.
2. **Stop assuming one process.** Anywhere a service relies on the in-process
   lock for correctness, treat it as a bug to be fixed in the schema (unique
   constraints, `add_if_absent` as a real upsert) rather than in Python.

**Migrate when any of these is true**, whichever comes first:

- you deploy anywhere that isn't the laptop, with more than one worker
- a second person has an account
- the background worker becomes its own process
- embeddings land

### Audio is not database data

Segment blobs belong in object storage, never in Postgres. Already true via
`BlobStore` — keep it that way. Audio is the bulk of your bytes and the most
sensitive thing you hold; it wants a different lifecycle, a different backup
policy and a different retention rule from rows.

---

## 2. What the AI costs — computed, not guessed

Assume an active user records **2 hours/day**. Groq prices
([multiple third-party sources](https://www.eesel.ai/blog/groq-pricing),
Sept–Oct 2026; Groq's own pricing page moved, so confirm before you budget):

| | Rate | 2 h/day | Per month | Share |
|---|---|---|---|---|
| STT (`whisper-large-v3-turbo`) | $0.04 / audio hour | $0.080 | **$2.40** | **94%** |
| Analysis (`gpt-oss-120b`) | $0.15 in / $0.60 out per 1M tok | ~$0.005 | **$0.15** | 6% |
| | | | **≈ $2.55/user/mo** | |

Analysis is cheap because 2 hours of speech is only ~23k input tokens — speech
is slow. **The entire prompt-engineering surface of this product costs 15
cents a month.** Never optimise it for cost; optimise it for correctness,
which is what you've been doing.

### The three levers that actually matter

1. **Don't transcribe silence.** Ambient wear is mostly non-speech. VAD before
   upload plausibly removes 50–80% of audio seconds. That is a **50–80% cut to
   94% of your bill** — nothing else on this page comes close. It is also
   exactly the Tier 1 media-suppression work, which means that feature pays
   for itself in infrastructure terms alone, before you count the quality win.
2. **Batch anything not needed live.** The Batch API halves STT to ~$0.02/hour
   with 24h–7d turnaround. A morning briefing computed overnight does not need
   real-time inference. Live path for what the user is waiting on, batch for
   everything else.
3. **10-second minimum billing per request.** Short segments are billed as
   10 seconds. Don't upload 3-second chunks — it triples the cost of the same
   audio. Check the current segmenting against this.

### Rate limits bite before cost does

This is the thing to plan around:

| Tier | Audio seconds/hour | ≈ continuous users |
|---|---|---|
| Free | 7,200 | **1** |
| Developer | 400,000 | ~55 |

Free tier is literally one user recording continuously — you are already at
the ceiling. Dev tier gets you to roughly dozens. **You will hit a rate limit
long before a bill you notice**, and the failure is user-visible (a session
that won't process) rather than a line item. Two mitigations, both already
half-built:

- Your `TranscriptionDriver` Protocol with `groq` / `local_whisper` / `fake`
  implementations means a second provider is a config change. Add one *before*
  you need it. Deepgram and Fireworks are the usual second seats.
- The job queue already persists `Job` records. Make 429 a *retry with
  backoff*, not a failure — a queue that absorbs rate limits is worth more
  than a higher tier.

### When to self-host Whisper

Do the arithmetic before being tempted:

> A ~$0.50/hour GPU VM is **$360/month**. At $0.04/audio-hour that buys 9,000
> audio-hours of hosted STT — about **300 users at 1 h/day**, assuming 100%
> GPU utilisation, which you will not get.

**Don't self-host below ~300 active users.** Below that it is strictly more
expensive *and* more operational work. You already have
`local_whisper_driver.py`; keep it for development and offline testing, which
is where it earns its keep.

---

## 3. Hosting

### Where it runs now

Laptop + Cloudflare Tunnel. Fine for one developer, and the tunnel is a phase
10 *requirement* (Google won't accept `http://` redirects off localhost), not
a convenience.

### Options, honestly compared

| Option | Monthly | Good for | The catch |
|---|---|---|---|
| **Laptop + tunnel** | ₹0 | today | dies when the lid closes |
| **Single VM** (Hetzner / Vultr / DO) | $5–12 | first 50 users | you are the sysadmin; one box = one outage |
| **Fly.io / Railway / Render** | $10–30 | 50–1,000 users | less control, egress pricing |
| **AWS / GCP** | $100+ | later, or an enterprise deal | complexity you cannot yet afford |
| **Oracle Cloud always-free** | ₹0 | a staging box | capacity is unreliable; don't put users on it |

Object storage, separately, and this one has a clear winner:
**Cloudflare R2, for zero egress fees.** Audio is large and you will re-read
it (re-transcription, playback, the integrity receipt). S3's egress charges
are a tax on exactly the access pattern a memory product has. R2 is
S3-API-compatible, so `BlobStore` barely changes.

### What I'd actually do

```
NOW         laptop + cloudflared                    ₹0
            └─ get Google OAuth working, keep building

FIRST USERS single VM + managed Postgres + R2        ~$20–25/mo
 (1–50)     ├─ api process + worker process (hence Postgres)
            └─ managed PG, not self-installed: backups are the point

GROWTH      Fly.io or Railway + managed PG + R2      ~$50–100/mo
 (50–1,000) ├─ scale the worker separately; STT is the bottleneck
            └─ second STT provider behind the Protocol

LATER       whatever the users and the money say
 (1,000+)
```

**Deliberately boring.** Nothing above needs Kubernetes, and the step that
matters — splitting the worker from the API — is forced by the architecture,
not by load. One VM with two processes and a real database beats a clever
serverless setup you can't debug at 2am when a user's whole day failed to
transcribe.

### Two things that are not infrastructure but will be decided by it

**Data residency.** You're in India; Groq is US-hosted. Under the DPDP Act,
sending voice recordings offshore is a question you will be asked the moment
you have a business customer, and the answer "we send everything to a US
inference provider" is one some of them cannot accept. Not a blocker now.
Worth knowing it is a *sales* problem that becomes an *architecture* problem,
and that `local_whisper_driver.py` is the beginning of the answer. **This is
also now a market opening, not just a risk:** Limitless switched its service
off entirely in the EU, UK, Brazil, Israel, South Korea and Turkey rather than
deal with this class of problem, and Bee ships only to US addresses.

**Retention.** Decide now, in writing, how long raw audio is kept, because it
determines your storage bill, your breach exposure and whether
re-transcription (fixing "Pull kit" retroactively) is even possible. My
recommendation: **keep audio, make the retention window a user-visible
setting, default it generously, and let them shorten it.** The ability to
re-transcribe is worth real money to a memory product, and a user who chose
the setting themselves is in a different legal and ethical position from one
who didn't.

---

## Summary

| Question | Answer |
|---|---|
| What database now? | JSON files behind a Protocol |
| Will it sustain users? | **No** — fails at the second *process*, let alone user |
| Migrate to? | Postgres + pgvector, one instance, no vector DB |
| When? | second process, second user, or embeddings — whichever first |
| Do now, cheaply? | freeze the Protocol; write conformance tests |
| Biggest AI cost? | STT, **94%** |
| Biggest lever? | VAD / don't transcribe silence — cuts 50–80% of that 94% |
| What breaks first at scale? | **Groq rate limits**, around dozens of users |
| Self-host Whisper? | not below ~300 active users |
| Host where? | one VM + managed Postgres + Cloudflare R2 |
