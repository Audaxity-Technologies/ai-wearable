# Feature proposals

Drafted 2026-11-21, after cataloguing Bee, Omi, Forth and PLAUD (see
[COMPETITIVE_LANDSCAPE.md](COMPETITIVE_LANDSCAPE.md)) and reading what
reviewers and privacy researchers say goes wrong.

Each item says what it is, which documented failure it answers, and what it
costs. Ordered by **defensibility × distance from what we already have** —
not by how impressive it sounds in a demo.

A note on the premise. The brief's instinct — "forgetting things,
unstructurability" — is right, but the market has moved past *capture*. All
four competitors capture well. **Three of four fail at deciding what
mattered**, and that is where the product is won. Every proposal below is
about judgement, not recall.

---

## Tier 1 — build these next. They defend the thing we are already best at.

### 1. Media and bystander-speech suppression

**What.** Classify each audio segment before analysis: is this a person
talking *to* the user, or a TV, a radio, a podcast, a video call on someone
else's phone, a shop announcement? Suppress extraction from non-conversational
audio, and label it in the transcript rather than deleting it.

**Why.** This is the single most-cited failure in the market. Forbes on Bee:
"dozens of to-dos a day, many of which are teased out of **entertainment
content that Bee has mistaken for things I said**." We will hit this the first
time someone records with a TV on, and our eval set has no case for it —
`remind_me.json` already quietly tolerates five spurious items from banter.

**How.** Cheap version first: a prompt rule plus an eval case built from a
recording made deliberately in front of a TV. Real version: an audio
classifier on the segment (music/speech/broadcast) before transcription, which
also saves Groq minutes. Start with the prompt, because it is an afternoon and
will catch most of it.

**Cost.** 1 day for the prompt + eval. 3–4 days for the classifier.

---

### 2. Owner voice enrolment, and nothing else

**What.** Enrol **only the user's own voice**, once, at setup. Use it for one
job: deciding whether a given utterance is the user speaking or somebody else.
Do not attempt to identify other people.

**Why.** Two reasons, and the second is the important one.

Speaker attribution is the #2 complaint in the market — Bee "confused them
with coworkers or attributed ideas vaguely to 'someone'" — and it is **our own
largest known defect**: the first real recording lost "Pulkit" to "Pull kit"
and gave his commitment to Manan. Our entire differentiator is *who committed
to what*. We are the product that can least afford to get the person wrong.

But enrolling *everyone* is a biometric database of people who never consented
— exactly what Forth designed, then explicitly refused to ship ("Owner voice
enrollment is not available"). Enrolling only the owner gets us the
commitment/assignment distinction, which is the part that matters, and leaves
us with one voiceprint belonging to the person who chose to give it.

**How.** 20-second enrolment on first run. Per-utterance similarity scoring.
Feeds `owner` resolution: "I'll send it" from the owner's voice → owner is the
user; from an unrecognised voice → `unknown` unless named. This makes rule 4
of the prompt enforceable instead of inferred.

**Cost.** 4–5 days. Needs a speaker-embedding model; check whether Groq or a
small local model can do it before committing.

---

### 3. The open-loops ledger

**What.** A standing, cross-conversation view of two lists: **what you owe
other people**, and **what other people owe you** — each with who, since when,
the quote it came from, and whether it has been mentioned again since.

**Why.** This is the feature that uses our speech-act data and that nobody
else *can* build, because nobody else records whether something was a
commitment or a suggestion. Every competitor produces a flat to-do list that
resets daily. "Pulkit said three weeks ago he'd send the compliance forms and
has not mentioned it since" is not a to-do — it is the thing the user actually
forgot, and it only exists if you kept `act` and `owner`.

It is also the honest answer to "forgetting things". People do not forget
tasks they wrote down. They forget **promises made to them in passing**.

**How.** Mostly a query over what the store already holds: tasks grouped by
owner, aged, filtered by `act in (commitment, assignment)`, cross-referenced
against later sessions for re-mentions. A new screen and a retrieval method;
very little new data.

**Cost.** 2–3 days. Highest ratio of value to work on this list.

---

### 4. Capture-integrity receipt

**What.** Per session, show exactly what was captured and what was not:
segments recorded, uploaded, transcribed, any gap in the timeline, and any
minute where the mic was muted, the app was killed or the phone locked.

**Why.** The complaint that makes people stop wearing these things is not bad
summaries — it is **not trusting them**. An Omi user: the pendant "sometimes
shows as disconnected, so they **miss critical data**." Limitless users found
recording silently switched off after a phone restart. A system that silently
captures nothing is worse than no system, because the user has stopped taking
their own notes.

We are unusually well placed here: the segmented outbox already knows every
segment's state, and the backend already refuses to analyse a session until
every segment has landed. We just never showed it.

**Cost.** 1–2 days, almost entirely UI over data we hold.

---

## Tier 2 — strong, once Tier 1 is in

### 5. Consent mode

A visible, audible, jurisdiction-aware recording state: a chime on start, the
indicator the OS already forces via the foreground service, and a per-session
"who was present and were they told" prompt. In all-party-consent
jurisdictions (≈11 US states; **California SB 1130** would additionally bar
disabling the indicator), default to requiring an explicit acknowledgement
before the first segment is analysed.

Answers the EFF's objection directly — bystanders cannot opt out of a device
they did not buy — and it is the one compliance feature that is cheap now and
expensive to retrofit after a law lands. **2–3 days.**

### 6. Ask, over people and projects rather than sessions

Ask already answers with citations. Extend retrieval to entity-scoped
questions: "what has Priya committed to?", "what changed about the investor
pitch?", "what did we decide and then un-decide?" The memory layer already
preserves conflicts instead of overwriting them, which is what makes the third
question answerable at all — and no competitor preserves contradictions.
**3 days.**

### 7. Weekly review

The morning briefing covers today. A weekly one covers the thing people
actually lose: decisions made and quietly reversed, commitments aging out,
people not spoken to in a month, projects that went quiet. Reuses the briefing
scheduler. **2 days.**

### 8. MCP server

Expose the user's own memory to Claude and ChatGPT over MCP, as Omi does. This
is a small amount of work for a large amount of reach, and it reframes
Audaxity as the *capture and judgement layer* rather than yet another chat
app competing with the model vendors. **2–3 days.**

---

## Tier 3 — later, or only if a user asks

- **Desktop capture for online meetings** (Bee and PLAUD both have it; PLAUD's
  works "without bots", which is the better approach). Real demand, but it is
  a second client, and the ingest API already accepts it.
- **More execution drivers** — Slack, Notion, Linear, WhatsApp. Phase 10 built
  the pattern; each additional one is a day. Do them on request, not
  speculatively.
- **Multi-language.** Competitors claim 25–112 languages. Groq Whisper is
  already multilingual; the prompts and date parser are not. For India,
  Hinglish code-switching matters more than language count, and our date
  regex will not survive it.
- **Shared projects / team plan.** PLAUD charges $35/seat. Do not touch it
  until the single-user product is loved — the JSON store's migration trigger
  (>3 concurrent users) arrives first anyway.

---

## Explicitly not recommended

**Summary templates.** PLAUD ships "10,000+ Professional Summary Templates".
That is not a feature, it is an admission: the product cannot tell what you
need, so it asks you to configure it. Our whole thesis is that a commitment
looks like a commitment whether it was said in a board meeting or a corridor.
Adding templates would concede the argument.

**An always-on default.** Bee defaults to listening with a 5-minute-to-24-hour
silence timeout. It is also the source of most of its over-extraction and all
of its privacy coverage. Deliberate capture is a product position, not a
limitation — and after the acquisitions, it is a *marketable* one.

**Identifying every speaker by voiceprint.** See proposal 2. The capability is
tempting and the liability is a biometric database of non-consenting people.

**Sending email.** The reasoning stands — a wrong calendar event costs a
correction, a wrong sent email costs a relationship — but the mechanism named
here was wrong. `gmail.compose` is a *restricted* scope that permits
`drafts.send` and `messages.send`, so it never made the backend incapable of
sending; `gmail.send` is the narrower scope of the two. Corrected
2026-11-21: no mail scope is requested at all by default, which is the only
version of this promise the Gmail API can actually keep. See
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## What I would actually do first

In order: **1** (prompt-level media suppression, one day, protects the thing
we just measured at 26/26), then **3** (open-loops ledger, because it is the
clearest demo of something no competitor can build), then **4** (integrity
receipt, cheap and buys trust), then **2** (owner enrolment, the hard one, and
the one that fixes our worst known defect).

Before any of it: **cloudflared**, because the Google connection built in
phase 10 cannot complete from a phone over plain HTTP, and background
recording still needs the native dev build that CMake is blocking.

---

# Addendum, 2026-11-21: two requested ideas

Both are good. One I'd start soon, one I'd deliberately stage. Added after the
original list because they change the roadmap's shape rather than slotting into
a tier.

## 9. Agentic voice commands — "I'm riding, draft this for me"

**What.** Speak a command to the device rather than merely being recorded by
it: *"draft a reply to Pulkit saying I'll send the forms Monday"*, *"what did
I promise Priya?"*, *"remind me about this when I'm home"*. The device acts,
or queues an action for approval.

**Verdict: yes — and it is far less new work than it sounds.**

Phase 10 already built the hard half. The `Action` → approval → executor
pattern with `GoogleOrLocalExecutor` is exactly the safety model an agentic
assistant needs: voice command is a new *input* to a pipeline whose output is
already approval-gated.

One correction to how this was originally argued. It said the agent "cannot
act irreversibly, by construction, because `gmail.send` was never requested".
That was wrong — `gmail.compose` permits sending. The irreversibility
guarantee now rests on **requesting no mail scope at all**, which is
stronger, and on approval gating for everything else. Worth restating
precisely, because an agentic feature is exactly where someone would lean on
that sentence.

What is genuinely missing is smaller than the feature sounds:

1. **A command path distinct from ambient capture.** Right now everything is
   ambient speech to be analysed. A command is addressed *to* the system and
   must be routed, not filed. Needs the wake-word work already started.
2. **Entity resolution.** "Draft a reply to Pulkit" requires knowing which
   Pulkit and which email address — phase B of
   [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md).
3. **Spoken confirmation.** Riding a bike, you cannot read a card. The reply
   has to be short, spoken, and honest about what was queued versus done.

**When.** After Tier 1 and after the `Person` entity exists. Attempting it
before entity resolution means an assistant that drafts emails to the wrong
person, which is the one category of error that destroys trust permanently.

**Cost.** 4–5 days once B is in. Mostly the command router and the audio UX.

---

## 10. Live deal-watch — "you can get that laptop cheaper elsewhere"

**What.** Mid-conversation with a shopkeeper about a product and a price, the
system browses for a better offer and tells you.

**Verdict: the best idea on this page, and the one I'd most insist on staging.
Build the non-live version first.**

This is the highest "would change my actual day" value of anything proposed,
and it is the only feature here with an obvious revenue model attached
(affiliate). It is also where I would most expect a naive build to fail, for
four reasons that have nothing to do with the idea's quality:

1. **It demands real-time, and nothing in this system is real-time.** The
   pipeline is record → close session → analyse. Deal-watch is worthless
   after the fact. Streaming analysis is the single largest architectural
   change available to you — new transport, partial transcripts, incremental
   extraction, cost per minute rather than per session.
2. **Product identification from speech is the hardest extraction in the whole
   product.** "This i5 for forty-five" must become an exact SKU. Which i5,
   which generation, how much RAM, which year. A wrong SKU produces a
   confidently wrong price, and telling someone they're being overcharged when
   they aren't is worse than silence — it costs them a negotiation.
3. **Live retail price data is the real project.** Amazon.in and Flipkart via
   scraping is fragile and legally grey; affiliate APIs are narrow. (The
   Bright Data tooling already in this workspace is the plausible path, and
   that is a genuine asset — but it is still a data pipeline, not a prompt.)
4. **Buzzing mid-negotiation is socially expensive.** Your phone lighting up
   while the shopkeeper watches, with a number that might be wrong, in a
   conversation you then have to continue.

### The staged version I'd build instead

**A post-conversation price-check card.** The session ends; the analysis
notices a product and a price were discussed; the app shows:

> You discussed a *Lenovo IdeaPad Slim 3 i5* at **₹45,000**.
> It's **₹41,200** on Flipkart and **₹42,800** on Amazon.in.
> *Prices checked 6 minutes ago.*

This captures most of the value — people rarely buy a laptop in the thirty
seconds before they leave the shop, and "I'll think about it and come back" is
the normal shape of that purchase. It needs **no streaming**, fits the batch
pipeline exactly as it stands, carries no social cost, and tests the two
genuinely uncertain things cheaply:

- can we identify products from speech reliably enough to quote a price?
- do people act on it?

If that card gets used, the live version earns its streaming rewrite. If it
doesn't, you learned it for a week of work instead of a quarter.

**Cost.** 1 week for the card, including a product-extraction eval set —
which should exist before the first price is ever shown to a user, for the
same reason the extraction eval exists. Streaming, later and only on evidence,
is 3–4 weeks.

### One design rule, from the start

Quote a price **only with a timestamp and a source link**, and say nothing when
confidence is low. A deal-watcher that is sometimes wrong and always confident
is a liability; one that occasionally says nothing is merely quiet. This is the
same honesty rule as the Google executor: the alternative to a correct answer
is silence, not a guess.

---

## Revised order, with the addendum folded in

1. **Tier 1 ×4** — media suppression, integrity receipt, open-loops ledger,
   owner voice enrolment
2. **Memory phases A–C** — place capture *first* (unbackfillable), then the
   `Person` entity, then the utterance index
3. **Price-check card (10, staged)** — the demand test, cheap
4. **Memory phases D–G** — question parser, bi-temporal edges, eval set
5. **Agentic voice commands (9)** — once entities are real
6. Live deal-watch, only if the card proves demand
