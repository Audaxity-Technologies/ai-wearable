# Retrieval memory: is this the right direction?

Ideation only. Nothing here is built. Drafted 2026-11-21 in response to:

> "I want to retrieve a person's memory — what did I and Pulkit decide that
> day. It should remember by name, place, time, everything... it should behave
> like a database of my brain, when I need to retrieve the memory in any form
> of question I am able to."

## Verdict first

**The direction is right. The framing has one flaw that will cost you months
if you don't fix it now.**

What's right: you have identified the only defensible position left in this
market. All four competitors capture well and none of them *remember* well.
Bee and PLAUD give you a searchable archive of transcripts. An archive is not
a memory. The question "what did Pulkit and I decide that day" is not a search
query — it is a *resolution* problem wearing a search query's clothes, and
nobody has solved it.

What's wrong: **"a database of my brain" says the problem is storage.** It
isn't. Storage is nearly free and you already do it. Every system in this class
fails at *retrieval under ambiguity* — turning "that day", "Pulkit", and "what
we decided" into one specific span of recorded speech. Build for storage and
you get a bigger archive. Build for resolution and you get the product you just
described.

Concretely, in your sentence above there are four separate hard problems and
only one of them is storage:

| Fragment | The actual problem | Status today |
|---|---|---|
| "Pulkit" | entity identity — Whisper wrote "Pull kit" | **broken** |
| "that day" | temporal resolution *of the question* | **unbuilt** |
| "decided" | you already store this — `SpeechAct.DECISION` | **done, and rare** |
| "by place" | geospatial context | **never captured** |

Three of four are not storage. And the fourth one — place — is the urgent one,
for a reason I'll come back to.

---

## What you already have that almost nobody else does

Worth being explicit, because it determines what to build next.

1. **Speech acts.** You record *why* an utterance mattered —
   commitment / assignment / decision / suggestion. "What did we decide"
   is a one-line filter for you and an LLM guess for everyone else.
2. **Provenance on everything.** `source_session_ids` + `source_quotes` on
   every memory and task. You can always answer "why do you believe this?"
3. **Conflicts preserved, not overwritten.** `Memory.conflicts_with` keeps
   both sides when a new conversation contradicts an old one.
4. **A layered extraction contract.** `RawAnalysis` → `ConversationAnalysis`,
   with Python resolving dates because the LLM is bad at arithmetic, and
   quote verification against the transcript.
5. **An eval harness with labelled cases.** This is the rarest one. You
   measured your last prompt change at 26/26 and caught two regressions you'd
   otherwise have shipped.

Point 3 is the most interesting, because it is a weaker version of the thing
the research literature has converged on, and upgrading it is the single
highest-leverage change available to you.

---

## The one big idea: make time a property of facts, not just of recordings

`conflicts_with` says *"these two statements disagree."* It cannot say **which
one was true in March.**

The state of the art — Zep's [Graphiti](https://arxiv.org/abs/2501.13956),
which reports 71.2% on LongMemEval against a 60.2% full-context baseline — uses
**bi-temporal** facts. Every fact carries two independent time axes:

- `valid_from` / `valid_to` — when the fact was **true in the world**
- `observed_at` — when **you heard about it**

These come apart constantly, and the gap is where human memory questions live:

> On 3 March, Pulkit says: "we moved the launch to June."

The fact *"launch is in June"* has `valid_from` = whenever they decided it
(possibly February, possibly unknown) and `observed_at` = 3 March. The earlier
fact *"launch is in April"* gets `valid_to` = 3 March — **invalidated, not
deleted**.

Now these questions all become answerable, and none of them is answerable with
`conflicts_with` alone:

- "What's the launch date?" → the fact valid *now*
- "What did we think in February?" → the fact valid *then*
- "When did the launch date change?" → the invalidation edge itself
- "Did Pulkit ever tell me about a date change?" → `observed_at` on the edge
- "How many times has this slipped?" → count the invalidations

That last one is the point. **A to-do list cannot tell you that your launch
date has moved four times.** A bi-temporal graph tells you without being
asked. No competitor can produce that sentence.

This is also a real-work estimate, not a free lunch: it is a schema change
plus an invalidation pass in `services/memory.py`, and it is much cheaper now,
with your data in JSON files and a handful of sessions, than after you have
users.

---

## The architecture I'd propose

Four layers. The discipline is in which layer an LLM is allowed to write to.

```
L3  NARRATIVE     rolling digests per person / project / month
    derived · disposable · LLM-written · NEVER cited directly
                          ▲  rebuilt from below, on demand
L2  GRAPH         entities (Person, Project, Place, Org) and
                  bi-temporal edges between them
    derived · rebuildable · LLM-proposed, rule-validated
                          ▲
L1  UTTERANCE     one row per utterance: speaker, addressee, absolute
                  timestamp, place, session, speech act, embedding
    derived · rebuildable · mechanical
                          ▲
L0  EPISODE       raw audio + transcript + spans + time + place + device
    IMMUTABLE · never summarised away · the only ground truth
```

**The rule that makes this trustworthy: every sentence in an answer must cite
L0 or L1. L2 and L3 may *find* things; they may never *be* the evidence.**

That one rule is what stops a memory system confabulating, and it is the
difference between your product and a chatbot with a long context window. You
already enforce the equivalent for extraction (quotes verified against the
transcript). This extends it to recall.

### Why L1 is the layer you're missing

Today your retrieval grain is the **session analysis** — one row per
conversation. For "what did Pulkit and I decide that day", the session is too
coarse: a two-hour recording contains one relevant sentence and you will
return the whole thing, or score it below a session that merely mentions
"Pulkit" five times.

The grain that matches the question is the **utterance**. This is also the
grain that makes `place` and `speaker` meaningful, because both change
mid-session — you walk from the office to a café, you talk to three people.

### Why L3 is disposable on purpose

Summaries drift. They absorb errors from the layer below and then get summarised
again. If you can rebuild L3 from L0–L2 at any time, a bad summarisation prompt
is a regenerate, not a data-loss event. If you *can't*, your brain database
slowly becomes a game of telephone with itself.

---

## The query path — this is the actual answer to "any form of question"

Most teams build: *question → embed → top-k → stuff in prompt → answer.*
That is the thing that will not work for you, and it's worth being precise
about why: `top-k` over a flat index has no way to express "that day", and
embeddings are specifically bad at the discriminations you need most —
*negation*, *time*, and *who said it*. "Pulkit promised to send the forms" and
"I promised Pulkit I'd send the forms" are near-identical vectors and opposite
facts.

**Structured-first, semantic-second:**

```
1. CLASSIFY        point lookup · temporal range · relationship ·
                   aggregation · existence ("did I ever...")

2. RESOLVE         mentions  → entity ids        ("Pulkit" → person_7)
                   time      → absolute range    ("that day" → 2026-03-03)
                   place     → place id
                   ── ambiguous? ASK. Do not guess. ──

3. FILTER          narrow L1 to tens of candidates using the resolved
                   structure, before any ranking happens

4. RANK            hybrid over the narrowed set:
                   BM25 + embeddings + graph proximity + recency

5. ANSWER          grounded in L0/L1 spans, with citations
                   ── nothing retrieved? SAY SO. ──
```

**Step 2 is the product.** It's also where I'd spend the engineering, and
it's the step everyone skips.

The `ASK` branch is a feature, not a fallback. "Which Pulkit — your cofounder
or Pulkit Sharma from Infosys?" is a *better* answer than a confident wrong
one, and it's a sentence that makes the user trust the system more every time
they hear it. Same for time: if "that day" has three candidates, show three
dates with one-line context and let them point.

### What this buys that a vector store cannot

| Question | Needs | Vector-only? |
|---|---|---|
| "What did Pulkit and I decide on the 3rd?" | entity + time filter + act filter | no |
| "What has Pulkit committed to and not delivered?" | graph traversal + aggregation | no |
| "Have I ever discussed pricing with him?" | existence over filtered set | no |
| "What changed about the pitch?" | bi-temporal invalidation chain | no |
| "What was that thing about the warranty?" | semantic similarity | **yes** |

One row of five. Embeddings are a **ranker inside a filtered set**, not the
retrieval system. Build them in that position and they help; build the system
around them and four of those five questions become unanswerable.

---

## The three gaps, in the order I'd close them

### 1. Entity identity (blocking everything else)

`owner` is a free string. `Memory.key` is `"person:manan"`. The moment Whisper
writes "Pull kit", you have two people — and your *first real recording*
already did exactly this.

You need a `Person` record with: a canonical name, an alias set ("Pulkit",
"Pull kit", "PK", "my cofounder"), optionally a voiceprint (see Tier 1
proposal 2), and a merge operation the user can trigger from the app. Pronoun
resolution ("he said he'd do it") belongs here too, resolved at extraction
time against the session's participant list, where the context still exists.

**Nothing downstream works without this.** A graph whose nodes are strings is
not a graph.

### 2. Question-side time resolution

You have `core/dates.py` resolving time phrases in *speech*. The mirror image —
resolving them in *questions* — is unbuilt, and it is roughly the same problem
with one extra move: anchoring. "That day" in a question anchors to whatever
the conversation was just about, not to today. "Last Tuesday" anchors to the
asking time. "When we were in Bangalore" anchors to a *place*, which is why
place matters.

Reuse the parser. Add an anchor parameter. This is days, not weeks.

### 3. Place — capture it this week, even unused

`Segment` and `Session` carry no location. Everything else here can be
backfilled by re-running a pipeline over stored audio. **Location cannot.**
You will never recover where the user was in March 2026 if you didn't write it
down in March 2026.

Add `coarse_place` to `Segment` now — city/neighbourhood granularity, not GPS
traces, with a clear toggle. Coarse is both the privacy-respecting choice and
the one that matches how people actually ask ("that conversation at the café",
"when I was in Delhi"). Precise coordinates buy you nothing for retrieval and
cost you a great deal in a breach.

This is a two-hour change that is worthless in six months if skipped.

---

## Honest limits — where "any form of question" stops

Worth knowing before you promise it to anyone:

- **Things nobody said out loud** are not recoverable. The most important part
  of a decision is often a nod. Set the expectation in the UI.
- **Precise counting is unreliable.** "How many times did I mention the
  pitch?" depends on capture coverage, which is never 100% — which is exactly
  why Tier 1 proposal 4 (the integrity receipt) exists. Answer with a range
  and a coverage caveat, or decline.
- **Transcription errors are permanent in the index** unless you keep audio
  and allow re-transcription. Keep the audio. Budget for it.
- **Early recall will feel worse than people expect**, because human memory
  retrieves on cues your index doesn't have — smell, mood, who was sitting
  where. Setting expectations honestly is cheaper than over-promising and
  being disbelieved on the one answer that's right.

---

## Build order

| Phase | What | Why here | Est. |
|---|---|---|---|
| **A** | `coarse_place` on `Segment` + `Session` | unbackfillable; do it first | **done 2026-11-21** |
| **B** | `Person` entity, aliases, merge UI | unblocks everything | **done 2026-11-21** |
| **C** | L1 utterance index (+ speaker, place, absolute time) | the missing grain | **done 2026-11-25** |
| **D** | Question parser: classify + resolve + **clarify** | the product | 5–6 d |
| **E** | Bi-temporal edges; `conflicts_with` → invalidation | the differentiator | 4–5 d |
| **F** | Embeddings as a ranker *inside* the filter | last, deliberately | 2–3 d |
| **G** | Retrieval eval set (question → expected citations) | **alongside D, not after** | ongoing |

**G is not optional and it is not last.** You already know this — the
extraction eval caught two regressions in your last change that code review
would not have. Retrieval has strictly more ways to silently degrade than
extraction does. Build the labelled set as you build D, or you will be tuning
in the dark within a week.

Phase F last is a deliberate, slightly contrarian call: embeddings make a bad
retrieval system *feel* better while making it harder to debug, because every
answer becomes plausible. Get the structure returning correct small candidate
sets first, where a wrong answer is visibly wrong.

---

## Phase A as built (2026-11-21)

Shipped before anything reads it, which is the whole point: every other phase
can be rebuilt from stored audio, and this one cannot.

**Coarse is enforced, not promised.** Coordinates are rounded to 3 decimal
places - about 110m - **twice**: on the device, so full precision never
crosses the network, and again on write, so the guarantee does not depend on
the client being correct. The second one is the mechanism; the first is good
manners. `test_coordinates_are_rounded_on_construction` is the test that
makes it real, because it sends full precision from a client that did not
round.

**One fix per segment, never a watcher.** `watchPositionAsync` would produce a
movement history, which is a different product and a different liability. A
single reading as each chunk closes answers "where did this conversation
happen" and nothing else. `Accuracy.Balanced` (~100m) is requested rather than
`High`, because asking for GPS precision and then discarding it would cost
battery and ask for more permission than the feature uses - and on Android it
lets the OS satisfy us with approximate location. Only
`ACCESS_COARSE_LOCATION` is declared.

**A place is never inherited.** Lose a fix mid-conversation and that segment
has `place = None`. Borrowing the previous segment's value would be the one
unrecoverable corruption available here, since nothing downstream could ever
tell the difference. There is a test for exactly this.

**Opt-in, default off,** with the irreversibility stated on the toggle:
turning it on later does not fill in past recordings. Refusing the OS prompt
stores the flag as `false` rather than leaving an on switch that captures
nothing.

**Three things deliberately not stored:** heading, speed and altitude; any
point from a moment when no audio was recording; and - in the logs - the
coordinates themselves. The ingest event records `has_place: true`, never
where, because logs travel further than the database does.

Place lives on `Segment`, not `Session`, because people walk from the office
to a cafe mid-conversation. `Session.places` is a summary computed at close,
ordered by time spent, so a 10-second place is not shown beside an hour-long
one as an equal.

**What is not built yet:** nothing *reads* place for retrieval. That is phase
D, and it needs the question parser. Until then this is pure capture - which
is exactly the trade, because the capture is what expires.

## Phase B as built (2026-11-21)

**The automatic rule I wrote first did not work, and a test said so.**

Despacing was meant to fix the real failure - Whisper hearing "Pulkit" as
"Pull kit" - on the reasoning that ASR word-boundary errors keep the phonemes
and only move the spaces. It doesn't: the mis-hearing also **doubled the L**,
so `despace("Pull kit")` is `"pullkit"`, not `"pulkit"`. The rule written
specifically for that bug would not have fixed that bug.

`squash()` now removes spacing *and* collapses runs of the same letter, which
does resolve it, and `test_despacing_alone_does_not_fix_the_real_bug` is kept
so nobody re-narrows it later. Gemination errors are a large, well-behaved
class in transcribed Indian names, which is why this stays automatic while
general edit distance does not: "Priya" and "Prithvi" score 0.50 and are not
the same person, and no threshold separates that from "Pulkit"/"Pulkit
Verma".

**Two tiers, and the gap is the product.** Automatic covers exact keys,
squashed keys, and token containment. Everything merely similar becomes a
`MergeSuggestion` that the user applies or ignores - and the suggestion
carries its *reason*, which can argue against itself: two similar names that
appear in the same conversation are usually two people, and the card says so
rather than showing a bare score.

Token containment ("Pulkit" ⊂ "Pulkit Verma") is automatic, which is a
deliberate personal-scale assumption rather than an oversight: in one
person's conversations those are the same man far more often than two. There
is a test asserting it, so if that stops being true it fails loudly instead
of drifting.

**`is_self` replaced display-name matching.** The ledger used to decide "do I
owe this?" by comparing the owner string to the account's `display_name`,
which fails on a nickname, a surname, and every recording where nobody is
named aloud. One tap - "this is me" - now settles it for every past and
future task. It is never inferred: guessing would mean deciding whose
commitments these are on a name match. Exactly one person can hold it, since
two would make the own/owed split meaningless.

**Merging discards nothing.** Names become aliases, sessions and mention
counts are added, and the absorbed id keeps resolving through `merged_from` so
tasks written before a merge are not orphaned into `unattributed` by it.
`is_self` is sticky in both directions.

**Pronouns are resolved at extraction, or not at all** (prompt rule 14).
Extraction is the only stage that can still see the conversation. The bar is
certainty rather than likelihood: one named candidate resolves, two candidates
do not, and `evals/cases/pronouns.json` asserts both in one transcript -
because a prompt can pass either alone by being uniformly eager or uniformly
cautious.

**Deleting a person does not delete their commitments.** Tasks keep their
owner string and fall back to "not attributed" - visibly unowned rather than
silently reassigned.

**What is still unbuilt:** the L1 utterance index (phase C) and the question
parser (phase D). Identity now exists for them to point at, which is the
whole reason B came before C.

## Phase C as built (2026-11-25)

One row per span, carrying **absolute** time, the segment's place, and the
speech act of any extracted item quoted from it. Built by the pipeline right
after analysis — including when the analysis degraded, because the spans and
their timestamps are still true and an index of what was said is useful
precisely when extraction failed.

**Derived and rebuildable, deliberately.** `scripts/rebuild_utterances.py`
drops a session's rows and rebuilds them from stored segments. The linking
rules will change as phase D learns what questions need, and a layer you
cannot rebuild is a layer you cannot fix.

**The linking rule was wrong on its first run against real audio.** It scored
spans against each item's `source_quote` with `quote_similarity`, which
returns 1.0 for any exact substring — and on short strings compares
single-word windows. `quote_similarity("Also", "So remind me")` is **0.67**,
so a four-character span was tagged as an `assignment`. Replaced with word
overlap plus a minimum length: containment counts only when the shorter side
is at least six characters, and otherwise two significant shared words are
required. That dropped the real session from 10 links to 8, and both removed
links were spurious. A quote spanning two spans still links both.

**Two things it refuses to infer.** `speaker_person_id` stays `None` until
voice enrolment is measured (roadmap 0a) — `owner` is who took the work on,
not who spoke, and "You should send the forms" has owner Pulkit and a
different speaker. And a span matching no item gets no act, which is the
ordinary case rather than a gap.

**Querying filters, it does not rank.** `act` + time range + place + text, at
sentence grain, returning `(page, total)` so a caller can tell "nothing
matched" from "a great deal matched". Phase D decides *which* filter a
question implies; phase F adds embeddings as a ranker inside a filtered set.
Shipping the filter first is what keeps a wrong answer visibly wrong rather
than plausibly wrong.

Verified against real recordings: `place=academic` + `act=commitment`
+ a five-minute window returns the three sentences actually spoken at that
place, out of 26 indexed. The session-grained retrieval could not express
that query at all.

## What I'd push back on

**"It should remember everything."** This is the Bee failure mode with a
nicer name. Indiscriminate recall is what produces dozens of junk to-dos a
day. The target you want is subtly but importantly different:

> ❌ "It should surface everything it heard."
> ✅ **"It should never be unable to find something it heard."**

Index everything. Surface precisely. Those are the same sentence in a pitch
deck and opposite products in practice.

**"In any form of question."** Mostly achievable, with the taxonomy above —
*provided* you build the structured layer. Pure semantic search gets you one
question type out of five and an air of confidence about the other four.

---

## Prior art worth reading before building

- **Zep / Graphiti** — [arXiv:2501.13956](https://arxiv.org/abs/2501.13956).
  The bi-temporal design above is theirs; it's the closest published thing to
  what you described. Note that their LoCoMo numbers are
  [disputed](https://github.com/getzep/zep-papers/issues/5); the LongMemEval
  results are better supported. Read the design, don't trust the leaderboard.
- **Mem0** — widest adoption, vector-first with an optional graph variant.
  Useful as the baseline you should beat on temporal questions, which is
  precisely where vector-first memory is weakest.
- **LongMemEval / LoCoMo** — the standard benchmarks. Don't adopt their
  scores; adopt their *question taxonomies* as the skeleton of your own eval
  set. They have already done the work of enumerating how people ask.
- **[LiCoMemory](https://arxiv.org/pdf/2511.01448)** and
  **[TransMem](https://arxiv.org/pdf/2611.10683)** — 2026 work on cognitive
  layering and on retrieval over *evolving* facts, which is your exact
  problem.

A closing note on build-vs-adopt: Zep and Mem0 are both adoptable today and
would get you to a demo faster. I'd still build it, for one reason — your
speech-act layer is the asset, and neither framework has a place to put it.
Adopting one means flattening commitments, decisions and suggestions into
undifferentiated "memories", which throws away the only thing you have that
Amazon doesn't. Read their designs; keep your schema.
