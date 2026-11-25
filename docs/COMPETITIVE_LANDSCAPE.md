# Competitive landscape

Researched 2026-11-21 from the vendors' own sites plus independent reviews.
Figures marked **(claim)** are the vendor's own and are not independently
verified — several are mutually contradictory even within one site.

**The headline is not a feature. Two of the four reference products are no
longer independent companies.**

| | Bee | Omi | Forth | PLAUD |
|---|---|---|---|---|
| Owner | **Amazon** | Based Hardware (indep.) | Sift Wiki, Corp. (indep.) | Plaud Inc. (indep.) |
| Status | Shipping | Shipping | **Pre-order, ships early 2027** | Shipping, 4 models |
| Hardware | Frontier $52.49, Eclipse $99.99 (pre-order) | Pendant $129, Glass dev kit $499 | Band $180 founder / $220 | ₹16,990–₹20,990 (India) |
| Battery | 7 days / 3 weeks | 2–3 days | not stated | 20–50 h recording |
| Free tier | 14 days of history | 5 h/mo *or* 300 cloud min (site contradicts itself) | not stated | 300 min/mo |
| Paid | $11.99/mo, $99.99/yr | $29.99/mo, $269/yr | **no pricing published** | $17.99–$29.99/mo; Team $35/seat |
| Open source | No | **Yes, software + hardware** | No | No |
| Compliance | Trail of Bits audit (claim) | "aligned with SOC 2 and HIPAA" (claim) | none claimed | **SOC 2 Type II, HIPAA, GDPR, EN 18031** (claim) |

---

## 1. What each one actually offers

### Bee — now an Amazon product

Owned by Amazon: the site carries an `Amazon.com, Inc.` copyright, new users
sign in with an Amazon account, and the AI runs on AWS Nitro.

- **Hardware.** *Frontier* ($52.49): wrist or clip, 7-day battery, IP67, dual
  mics, single button — press to start/stop, long-press to leave a note,
  double-press to bookmark. *Eclipse* ($99.99, pre-order): 4.6 mm MagSafe
  card that rides on the phone, 3-week battery, and **processes both sides of
  phone calls**.
- **Software.** "Conversation summaries and search", "Your timeline of
  memories", "Suggested To-Dos", "Personal patterns and insights", "Draft
  emails and events", "Unlimited Memory", "Bee for Desktop" (captures Zoom
  from the computer), "Conversation Templates" for work/health/relationships,
  40+ languages.
- **Integrations.** Gmail, Google Calendar, Outlook, Photos, Health, **Alexa**
  ("What are my Bee to-dos?"), **Amazon.com shopping suggestions**.
- **Privacy.** "Bee Private Compute" with customer-owned keys that "never
  leave the device", audio "processed instantly and then deleted", a
  Trail of Bits audit and Sigstore ledgers **(claim)**. Note the gap: the
  linked privacy page is Amazon's *general* privacy notice, with no
  Bee-specific retention schedule.
- **Silence timeout** is user-configurable from 5 minutes to 24 hours.

**Read:** the distribution advantage is now overwhelming — Alexa, Prime
delivery, and a $52 price that is below plausible hardware cost. Bee is no
longer a startup competitor; it is Amazon's beachhead.

### Omi — the open-source one

- **Hardware.** 2.5 × 1.5 cm pendant, $129, 2–3 day battery, 2 mics,
  Bluetooth + Wi-Fi, **records offline and syncs later**. Separate $499
  camera-and-audio glasses dev kit.
- **Software.** "AI summaries, to-dos and mind maps in 25+ languages", "Daily
  Recaps", "Ask Omi" (30 questions/mo free, 1,000 on Pro), live transcript,
  speaker identification ("Know who said what"), speech profiles.
- **Platform.** This is the real differentiator: **open-source software and
  hardware** on GitHub, an **Omi App Store**, developer webhooks (audio bytes,
  realtime transcripts, memory creation, day summaries), bounties, and an
  **MCP server** so your Omi memory is usable from ChatGPT and Claude.
- **Integrations.** Google Calendar, Gmail, Telegram, Apple Health, Apple
  Reminders, Todoist, ClickUp, Asana, Google Tasks, ChatGPT, Claude, MCP.
- **Privacy.** AES-256 at rest, no training without permission **(claim)**.
  Worth noting what it collects that the others do not: **precise GPS,
  background location, and screen snapshots** for conversation context. The
  processor list is long — OpenAI, Anthropic, Google, OpenRouter, Perplexity,
  Soniox, Modulate, Deepgram.
- **Claims** 300,000+ professionals.

**Read:** the ecosystem play. An app store and MCP make Omi the one a
developer would pick, and the one whose users are least likely to churn.

### Forth — not shipped yet

- **Status.** Band pre-orders only, **ships early 2027** (slipped from
  November 2026). 500 founder units at $180. **No subscription pricing has
  been published at all.**
- **Concept.** Two halves: the Band captures, and **"Cue"** is an agent you
  text **in iMessage** — no app. Cue has **its own browser** and a **Vault**
  of saved logins where a filling component supplies secrets to approved sites
  *without the model seeing the value*.
- **Software.** Memory split into "People", "Plans", "Files", each entry
  tagged with where it was heard ("heard Sep 3 · lunch with Dave"). Explicit
  deletion verbs: "Forget this conversation", "Forget a person", "Delete
  everything". Cue **"asks before it sends, books or posts anything"**.
- **Integrations.** Gmail, Google Calendar, Slack, GitHub, Notion, Linear,
  WhatsApp, Google Meet — plus its own browser for anything unconnected.
- **Retention.** The most aggressive: "right after the summary is written, the
  audio **and the transcript** are deleted, with no backup copies." Memory
  keeps only facts plus short evidence quotes.
- **Owner voiceprint** is designed but explicitly **not built**: "Owner voice
  enrollment is not available."

**Read:** Forth is the closest competitor to Audaxity's thesis — approval
before action, facts-not-recordings, evidence quotes attached to memory. It is
also **a year from shipping, with no price**. That is the window.

### PLAUD — the professional incumbent

- **Hardware.** Four shipping models, all 64 GB. *Note Pro* (₹20,990): credit-card
  shaped, 2.99 mm, AMOLED display, **4 MEMS mics + 1 VPU**, 30–50 h recording.
  *NotePin / NotePin S* (₹16,990 / ₹19,990): wearable pin, 20 h.
- **Software.** "AI Summary", **"10,000+ Professional Summary Templates"**,
  "Plaud Agent", "Ask Plaud", "Artifacts" (reports, decks, spreadsheets),
  "Routines" (scheduled recurring work), "Skills" (custom + community-shared),
  "Memory", "Knowledge base", "Plaud Desktop" (captures online meetings
  **without bots**), **112+ languages**.
- **Metering.** Minutes, strictly: 300/mo free, 1,200 on Pro, unlimited on
  Unlimited (capped at 24 h/day). Minutes **do not carry over**; when they run
  out only transcription pauses.
- **Compliance.** The strongest of the four: **SOC 2 Type II, HIPAA, GDPR,
  EN 18031**, regional data residency (US / Frankfurt / Japan / Singapore),
  zero-retention DPAs with every LLM provider, opt-in-only training **(claim)**.
- **Audience.** Explicitly professional: executives, sales, clinicians,
  lawyers, educators. **Claims** 3 million professionals.

**Read:** the one to beat on trust and the one to avoid imitating on
complexity. 10,000 templates is a confession — it means the product cannot
work out what you need without being told.

---

## 2. What the market just did

Two acquisitions in roughly a year:

- **Bee → Amazon.** Now sells at $52.49 with Alexa integration.
- **Limitless → Meta** (announced 5 Dec 2025). Pendant sales **ended**. The
  team went to Reality Labs. Revenue was only ~$2M against $33M+ raised.

And the detail that matters most to us: **Limitless shut its service off
entirely in the EU, UK, Brazil, China, Israel, South Korea and Turkey on
19 December 2025.** Bee ships to **US addresses only**.

Sacra's read is that the neck-and-chest form factor is being absorbed into
glasses and earbuds rather than surviving alone. That is a real risk to the
band. It is also an argument for the architecture already chosen here: **the
band is one of three clients of the same ingest API**, so if the category
moves to earbuds, the backend does not change.

---

## 3. What users actually complain about

From independent reviews rather than vendor pages.

**1. Over-extraction is the most damaging failure.** Forbes on Bee: it
"suggests dozens of to-dos a day, many of which are teased out of
**entertainment content that Bee has mistaken for things I said**." Reviewers
also report it "fixates on something irrelevant and misses the actual
important stuff."

**2. Speaker attribution is broken across the board.** Forbes again: many Bee
summaries "confused them with coworkers or attributed ideas vaguely to
'someone'", and Bee "outright thinks additional people were present."

**3. Noisy rooms wreck everything downstream.** "Transcription errors
propagate into summary errors, so action items can be misattributed,
decisions mischaracterized, and key context missed."

**4. Reliability, not intelligence, is what makes people stop wearing it.** An
Omi user: the click-and-buzz confirmation is "EXTREMELY inconsistent", and the
pendant "sometimes shows as disconnected, so they miss critical data."
Limitless users reported settings silently switching off after phone restarts.

**5. Summaries that are too thin are as useless as too thick.** Forbes on
Limitless: summaries "are not as helpful as some other AI wearables" because
they are too succinct.

**6. Bystander consent is the unresolved one, and it is becoming law.** The
EFF's position is that ambient recording makes privacy a consent question for
people who never bought the device and cannot opt out. Vendors point at an
indicator light; critics note a light only works if bystanders can see it.
**California SB 1130** would require explicit consent before recording
identifiable people where they have a reasonable expectation of privacy, and
**bar disabling the recording indicator** (reported sent to the Governor
4 Sep 2026 — status not independently confirmed). Roughly 11 US states require
all-party consent. India's **DPDP Act 2023** applies to us directly.

---

## 4. Where that leaves Audaxity

Four gaps, in order of how defensible they are.

1. **Nobody classifies speech acts.** Every competitor extracts "to-dos".
   None distinguishes *"I'll do it"* from *"maybe we should"* from *"can you?"*
   That distinction is the direct fix for complaint #1, and it is already
   built and measured here at 26/26 on the eval set.
2. **Nobody verifies that an item was actually said.** Quote verification —
   dropping any item whose `source_quote` cannot be found in the transcript —
   is a structural answer to inventing to-dos from a TV in the background.
3. **Two of four are owned by companies whose business is advertising and
   retail.** "Not owned by Amazon or Meta" is a real position, and it is the
   only one of these four advantages a competitor cannot copy.
4. **India and the EU are being abandoned.** Limitless left both. Bee ships
   only to the US. PLAUD charges ₹17–21k. A DPDP-native product priced for
   India has no serious incumbent.

The thing we should be most worried about: **speaker diarization**. It is the
#2 complaint in the market and it is also our own largest known defect — the
first device recording transcribed "Pulkit here" as "Pull kit here", lost a
participant, and attributed his commitment to the wrong person. Our
differentiator is *who committed to what*. Getting the person wrong is worse
for us than for anyone else on this list.

---

## Sources

- [Bee](https://bee.computer) · [Bee Pro](https://bee.computer/bee-pro) · [Bee Private Compute](https://bee.computer/blog/private-ai)
- [Omi](https://omi.me) · [Omi pricing](https://omi.me/pricing) · [Omi privacy](https://omi.me/privacy)
- [Forth](https://getforth.co) · [Forth product facts](https://getforth.co/facts/) · [What Forth can see](https://getforth.co/privacy/)
- [PLAUD India](https://in.plaud.ai) · [Device comparison](https://in.plaud.ai/pages/plaud-device-comparison) · [Plan pricing](https://www.plaud.ai/pages/plaud-ai-plan-pricing) · [Trust Center](https://www.plaud.ai/pages/trust)
- [Forbes: Best AI Wearables](https://www.forbes.com/sites/forbes-personal-shopper/article/best-ai-wearables/)
- [Bee AI Pendant Review 2026](https://www.layer3labs.io/gear/reviews/bee-ai-pendant) · [AI Wearables honest review](https://bigguyonstuff.com/ai-wearables-2026-honest-review/)
- [Omi user feedback: good and bad](https://feedback.omi.me/p/my-current-experience-good-and-bad)
- [I tried the Limitless AI and Omi AI](https://mikecann.blog/posts/i-tried-the-limitless-ai-and-omi-ai-so-you-dont-have-to)
- [Sacra: Why Meta bought Limitless](https://sacra.com/research/why-meta-bought-limitless/) · [Neowin: Meta acquires Limitless, ending Pendant sales](https://www.neowin.net/news/meta-acquires-ai-startup-limitless-ending-sales-of-its-popular-pendant-wearable)
- [AI wearables and the bystander consent gap](https://thesignal.in/technology/2026/09/17/ai-wearable-bystander-consent-gap-2026) · [Is your AI wearable allowed to record others?](https://memx.app/blog/ai-wearable-recording-consent-law/) · [EFF on AI wearables and privacy](https://ai-tldr.dev/releases/eff-ai-wearables-shift-privacy/)
- [What is a wearable AI recorder](https://vibe.us/blog/what-is-wearable-ai-recorder/)

---

# Addendum, 2026-11-26: Neo Sapien, and what is actually left

Researched after the user named [neosapien.ai](https://neosapien.ai/), which
had not been in the original set. It should have been: **it is the closest
competitor this product has, and it is Indian.**

## Neo Sapien Neo 1 — the competitor that matters

| | |
|---|---|
| Price | **Rs 12,999** (MRP Rs 15,999), **no recurring subscription** |
| Hardware | 20g pendant, metal chain, 16h battery, Bluetooth to phone, not water-resistant |
| Shipping | India only; US via Amazon. Dispatched in 2-3 days |
| Claim | "Meet world's best multi-lingual AI notetaker" |

**What they ship that we do not:**

1. **An MCP connector.** "Bring Neo 1 Memories to Any AI... Use everything
   Neo 1 remembers inside ChatGPT, Claude and more." That is
   [FEATURE_PROPOSALS.md](FEATURE_PROPOSALS.md) Tier 2 item 8, shipped.
2. **Desktop capture for online meetings, no bots.** "Dedicated desktop app
   to capture all online meetings. No bots." Zoom, Meet, Teams, WhatsApp.
   That is our Tier 3 item, shipped.
3. **Owner voice fingerprinting.** "Neo 1 recognizes your voice, providing
   accurate and personal responses." That is roadmap 0a - **and it is the
   same design we chose**: the owner only, not everybody. Independent
   convergence on that call is reassuring.
4. **140+ languages, Hinglish explicitly.** Our Tier 3 multi-language item.
5. **Projects**, a daily/weekly plan summary, and a pre-meeting briefing.
6. **Offline capture** on the device, processed when the phone reconnects.

That is four roadmap items already in a shipping product at Rs 12,999 with no
subscription. Anyone planning to monetise MCP access, desktop capture or
language coverage should know those are now table stakes in this market
rather than differentiators.

**What they do not do, from their own pages:**

1. **Who said what.** The only speaker claim is recognising *the owner's*
   voice. Diarisation is not mentioned anywhere. Their own comparison blog
   poses "can it still identify who said what?" as a question for buyers and
   **does not answer it for themselves**.
2. **No speech acts.** "To-dos that write themselves" and "extracts to-dos,
   commitments, and follow-ups" - a flat list. Nothing distinguishes a
   commitment from a suggestion from a decision, so nothing can answer "what
   did we decide and then un-decide".
3. **No change tracking.** "Infinite memory... Neo 1 remembers everything"
   is a storage claim. Nothing about what was true *then*.
4. **No capture integrity.** Their stated limitation is "requires wearing
   consistently to build value over time" - the problem named, with nothing
   offered for it.
5. **No consent feature.** Only advice: "Always ensure you have appropriate
   consent from participants before recording."
6. **Unnamed certification.** "audited by 3rd party security agencies to
   ensure that all information is 100% secure and private" names no agency,
   no standard, no report. "100% secure" is not a claim a security auditor
   would sign.

## The one place they are ahead of us on principle

**They delete the audio.** "Neo 1 only uses the audio file to transcribe
information. It does not store any audio recordings." "Audio files are not
stored once processed."

That is a genuinely stronger privacy posture than ours, and it should be said
plainly rather than spun. It also costs them something we decided to keep:
**re-transcription**. We can fix "Pull kit" retroactively, re-run a better
model over last March, and rebuild the whole utterance index from source.
They cannot - their index is as good as the model that was running the day
you spoke.

That is a real trade with a real cost on each side. Ours is defensible only
if the retention window is the user's choice and plainly stated, which is why
[SCALING_AND_HOSTING.md](SCALING_AND_HOSTING.md) puts it in the user's hands.

## The wider set, as of late 2026

| Product | Owner | Price | Note |
|---|---|---|---|
| **Neo Sapien Neo 1** | independent, India | Rs 12,999 | closest competitor; no subscription |
| **Bee** | **Amazon** | $49.99-$74.99 | US addresses only |
| **Limitless** | **Meta** | discontinued | pendant sales ended 5 Dec 2025 |
| **Omi** | independent | $89-$179 | open source |
| **PLAUD NotePin** | independent | ~$179 | deliberate press-to-record |
| Fieldy, Friend, UMEVO, Vibe Dot, Legend, soundcore Work | various | $?-$499 | a crowded long tail |

Two of the four original reference products now belong to Amazon and Meta.
The independent field is crowded at the bottom and empty at the top: nobody
is selling *judgement*, and almost everybody is selling capture plus
summaries.

## So what is actually left

Measured against all of them, four things are ours and nobody else's:

1. **Speech acts as data.** Commitment / assignment / decision / suggestion,
   stored and queryable. Every competitor flattens these into a to-do list.
   This is the one that makes the rest possible.
2. **Who owes what, across conversations.** The open-loops ledger needs
   `act` + `owner` + provenance, which none of them keep.
3. **What was true then.** Bi-temporal validity - "your launch date has moved
   three times". A storage claim cannot produce that sentence.
4. **Honest capture.** The integrity receipt separates a gap from a silence.
   Their limitation section names this exact problem and offers nothing.

And one new thing, built the day this was written: **participant labelling**.
Neo Sapien recognises the owner's voice and says nothing about anyone else;
we let the user name who was in a conversation after the fact, which is
*certain* evidence rather than probabilistic, and it accumulates across
conversations. "What has Pulkit committed to" is a question none of them can
answer and we now can.

The honest summary: we are behind on breadth and ahead on judgement. Breadth
is catchable with money and time, and four of our roadmap items just became
less valuable because a Rs 12,999 device already has them. Judgement is the
part that took the eval sets, the speech-act schema and the three bugs we
caught by running things over real recordings - and it is the part that does
not fall out of a feature list.
