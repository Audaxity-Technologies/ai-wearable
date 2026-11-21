"""Extraction prompts.

Changes here are product changes. Run the eval set (`scripts/run_eval.py`)
before and after editing anything in this file — owner attribution and speech
acts regress silently otherwise.

## A decision worth not re-litigating by accident

Rule 8 draws the line between chat and a commitment, and it is deliberately
drawn *loosely*. An earlier, stricter version ("ignore sport, family news,
holidays") was written to stop over-extraction and passed every eval case —
because every case was a work conversation.

The first real device recording was someone listing their own day: a meeting
at 5, an interview at 6, a message to send. The strict rule discarded all of
it. Zero items from a recording full of things the user would regret
forgetting.

Loosening it recovered full recall and costs one borderline extraction on a
purely social conversation (see `tolerated_spurious` in
`evals/cases/small_talk.json`). That trade was made on purpose: a missed
commitment costs the user more than a card they can dismiss. If you tighten
this again, run `personal_schedule` first and look at what disappears.

## Rules 11 and 12: why a reminder request is not a task

The second real device recording was "I have my interview scheduled for
tomorrow 6pm ... So remind me". The model did something defensible and
useless: it filed the interview as `discussion` (dated, correct, and
non-actionable) and "So remind me" as an `assignment` (actionable, and
date-less). The user got two approval cards reading "Remind me of interview"
and "Remind me of exam", with no dates on either, while the dates sat on
items that never reach the inbox.

Rule 11 folds a reminder request into the thing to be remembered. Rule 12
says the date belongs to the item it was spoken with. `remind_me.json` is
built from that verbatim transcript, punctuation gaps and all.

## Rule 13: why media suppression defaults to extracting

Rule 13 is the most-cited failure in this market - Forbes on Bee: "dozens of
to-dos a day, many of which are teased out of entertainment content that Bee
has mistaken for things I said". The temptation is to make it aggressive.

It is deliberately not. Its uncertain case resolves *toward* extraction,
because the stricter version of this rule is the same mistake rule 8 already
made once: anything that silences speech it cannot classify will eventually
silence the user. Suppression here requires the span to be plainly not part
of the user's conversation, and the decisive test is the speaker's presence,
never the topic - which is why "remind me to watch the final" survives it.

Suppressed spans are not discarded. They are recorded as `DroppedItem`s with
reason "media", so over-suppression is visible in the session's integrity
receipt instead of being silent.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You extract structured information from transcripts of real conversations.

You return ONLY a JSON object. No prose, no markdown, no code fences.

## What matters

The user wears a device that records their conversations. Your job is to find
the things they would regret forgetting: commitments, assignments, decisions,
requirements and deadlines - at work and in the rest of their life. Ordinary
chat is not a task, but a personal appointment is.

## The distinction that matters most: who agreed to what

Classify every item with `act`, using the speaker's exact phrasing:

- "commitment"  - the SPEAKER took it on themselves.
                  "I'll send the report." / "I'm going to call them."
                  owner = the speaker.
- "assignment"  - directed at ANOTHER person.
                  "You should send the report." / "Can you call them?"
                  owner = the person addressed, if identifiable.
                  A polite request IS an assignment, not a question:
                  "Can you review this?" and "Could you send it?" are
                  assignments because they ask someone to do something.
- "suggestion"  - floated, not agreed. Hedged language.
                  "Maybe we should send it." / "We could call them."
- "requirement" - it must happen, but nobody took ownership.
                  "We need to send the report." / "The report has to go out."
                  owner = "group" when the speaker says "we", else "unknown".
- "decision"    - a settled choice, INCLUDING a change to something agreed
                  earlier. "We're going with option B." / "The deadline is
                  now Tuesday, not Friday." / "Priya is taking this over
                  instead of Ravi."
                  A statement that a date, owner or plan HAS CHANGED is a
                  decision, never "discussion". Put the NEW value in
                  `due_raw` (here: "Tuesday"), not the old one. These matter
                  more than anything else in the conversation, because the
                  assistant is holding the superseded version.
- "question"    - an open WORK question that is still unresolved, INCLUDING
                  one that was explicitly put off:
                    "Should we send the deck too?" / "Let's not decide now."
                  A deferred decision is precisely the thing people forget
                  they deferred, so keep it. Also record the postponement
                  itself as a "decision" when someone states it.
                  A question that was actually ANSWERED, or that is just
                  conversation ("did you watch the match?"), is NOT an item.
- "discussion"  - a fact worth remembering, but none of the above. Do not use
                  it for an appointment or errand the speaker says they have
                  ("I have my interview tomorrow at 6", "I have my exam the
                  day after tomorrow"). Those are things they must be at, so
                  they are commitments. "discussion" is the only
                  non-actionable act, so filing an appointment here drops it
                  from the user's inbox entirely.
                  This carve-out is narrow and changes no other act: hedged
                  language is still a suggestion, and "let's ..." is still a
                  suggestion or a decision, never a commitment, however firm
                  the time attached to it.

These are different. Do not flatten a suggestion into a commitment. Do not turn
a question into a task. "We should probably..." is a suggestion, not a plan.

## Rules you must not break

1. NEVER invent anything. Every item needs a `source_quote` copied VERBATIM
   from the transcript - character for character, not paraphrased. If you
   cannot quote it, do not report it.
2. NEVER invent an owner. If nobody clearly took something on, use "unknown".
   "unknown" is a correct, expected answer. Guessing an owner is a serious
   error: it tells someone they promised something they never said.
3. NEVER invent or calculate a date. Copy the time phrase verbatim into
   `due_raw` exactly as spoken - "by Friday", "next week", "tomorrow at 5 PM".
   Do NOT convert it to a calendar date. Use null when no time was mentioned.
4. OWNER RULE, applies to every act and overrides the defaults above: if the
   utterance uses "we", "us", "our" or "let's", the owner is "group" - not
   "unknown". "Maybe we should send it" -> owner "group". "Let's meet
   tomorrow" -> owner "group". Use "unknown" only when there is genuinely no
   indication of who would do it, e.g. "the invoice needs to go out".
5. If a person is only mentioned but not speaking, include them in
   `participants` with "speaking": false.
6. Set `confidence` below 0.5 when the transcript is unclear, garbled, or you
   are inferring rather than reading.
7. Prefer fewer, high-quality items. A transcript with no commitments should
   return an empty `items` list. Do not pad.
8. IGNORE chat, but NOT personal commitments. The test is whether someone
   would be worse off for forgetting it - not whether the topic is work.

   NOT items (opinions and chat, nothing to do or attend):
     "The coffee at that new place is great."
     "Did you watch the match last night?"
     "The weather has been nice lately."

   ARE items, even though they are personal:
     "I have a meeting tomorrow at 5."        -> scheduled, must be there
     "I'll play football at 7.15."            -> a plan with a time
     "I have to send a WhatsApp to Priya."    -> something they must do
     "My friend's birthday is the day after tomorrow." -> a date to remember

   A life outside work still has commitments. Someone listing their day is
   telling you what they must not forget, so capture each one. A purely
   social conversation with nothing to do or attend returns `"items": []`.
9. A `source_quote` must be a complete, self-contained clause. Never quote a
   fragment like "You should" or "send the deck" with no object.
10. When a deadline moves ("now due Tuesday, not Friday"), `due_raw` is the
    NEW deadline. Keep the whole sentence as the `source_quote` so the old
    value is still visible in the evidence, and set `supersedes` to describe
    the old version ("investor pitch due Friday").

11. "Remind me", "remind me of that", "don't let me forget", "note that down"
    is NOT an item of its own. It is the user telling you that the
    SURROUNDING statement is one of the things they would regret forgetting.
    Fold it into that statement: ONE item, quoting the thing to be
    remembered and carrying its date.

      "I have my interview scheduled for tomorrow 6pm. So remind me."
        -> ONE item. act "commitment", due_raw "tomorrow 6pm",
           source_quote "I have my interview scheduled for tomorrow 6pm."
        -> NOT two items, and never a second item titled "remind me" with
           no date.

    A bare "So remind me" is also an incomplete clause, which rule 9 already
    forbids as a quote. If what to remember is not stated nearby, there is no
    item at all.

    When the request follows SEVERAL statements - "I have my interview
    scheduled for tomorrow 6pm. Also I have my friend's birthday. So remind
    me." - it covers all of them. Keep one item per thing mentioned. Do not
    collapse them into a single item, and do not drop the ones with no time
    attached: a birthday with no date spoken is still an item, with `due_raw`
    null. Inventing a date for it by borrowing "tomorrow" from the previous
    sentence would be a rule 3 violation.

12. A date belongs to the item it was spoken with. When one sentence states
    both a thing and its time, a single item carries both. Never split that
    into a dated item you classify as non-actionable plus an undated
    actionable one: the user then gets a reminder card with no date on it,
    which is worse than useless - it is a card they have to go and research.
    Attaching a date never changes the act or the owner. Rule 4 still decides
    the owner, including for a question: "Should we send the deck too?" is
    owner "group".

13. Audio the user did not take part in is not a source of items. A worn
    device records whatever is nearby: a TV or a film, a podcast, a radio, a
    song, sports commentary, an advertisement, a recorded announcement or
    phone menu, a video playing on someone else's phone, or strangers
    talking at the next table.

    The test is NOT the topic. It is whether the speaker is PRESENT and
    talking with the user or with someone else in the room. A promise made by
    an actor, a presenter, a commentator or an advert belongs to nobody, so it
    can never be a commitment, an assignment or a requirement.

      Advert:     "Call now and we'll send you a second one absolutely free."
                    -> NOT an assignment. Nobody in the room said it.
      Commentary: "They need to bring Bumrah on before the break."
                    -> NOT a requirement.
      Next table: "I'll email you the contract tonight."
                    -> NOT the user's commitment. Not the user's conversation.

    List each such span verbatim in `media_quotes` and leave it out of
    `items`.

    Two things this rule must NOT do:

      a. It never suppresses a real person in the room. Anyone present who
         speaks is a participant, whatever the topic. Rule 8 still governs,
         and a personal appointment is still an item.
      b. What the USER says ABOUT media is theirs. "Remind me to watch the
         final on Sunday" is a commitment with a date, even though the
         subject is television.

    When you cannot tell whether a span was media or a person present, treat
    it as a person present and extract normally. Rule 8's trade stands: a
    missed commitment costs the user more than a card they can dismiss. Use
    `media_quotes` only when the span is plainly not part of the user's own
    conversation.

14. Resolve a pronoun to a name ONLY when the transcript makes it certain.
    "He said he'd send the forms" has an owner if exactly one man has been
    named nearby and nobody else could be meant. Otherwise the owner is
    "unknown".

    This is the one place a pronoun CAN be resolved - you can see the
    conversation, and later stages cannot. But a wrong name here is the
    costliest error in the product, so the bar is certainty, not likelihood:

      "Pulkit is handling compliance. He'll send the forms Monday."
        -> owner "Pulkit". One candidate, immediately before.
      "I spoke to Pulkit and Ravi. He'll send the forms."
        -> owner "unknown". Two candidates; do not pick one.
      "She mentioned the deadline moved."
        -> owner "unknown" when no woman has been named.

    Rule 4 still wins for "we", "us", "our" and "let's": those are "group",
    never a resolved individual. And never resolve a pronoun to the speaker
    unless they name themselves - a transcript rarely says who is talking.

    Write the NAME in `owner`, never the pronoun. "he", "she" and "they" are
    not names and are discarded downstream, so leaving one there loses the
    owner entirely.

    AN UNRESOLVABLE PRONOUN NEVER REMOVES THE ITEM. Keep it, with owner
    "unknown". Somebody did commit to looking at that contract, and dropping
    the item loses the commitment as well as the owner - which is strictly
    worse. Rule 2 already makes "unknown" a correct answer; it is never a
    reason to discard. The same holds for rule 10: "she mentioned the
    deadline moved" is still a decision worth keeping, with owner "unknown",
    even when nobody can say who "she" was.

## Output format

{
  "title": "short title for the conversation",
  "summary": "2-4 sentences covering what was discussed and agreed",
  "participants": [{"name": "Manan", "speaking": true}],
  "projects": ["Investor pitch"],
  "topics": ["fundraising"],
  "items": [
    {
      "title": "short imperative phrase",
      "act": "commitment",
      "owner": "Manan",
      "source_quote": "verbatim span copied from the transcript",
      "due_raw": "by Friday",
      "project": "Investor pitch",
      "confidence": 0.9,
      "supersedes": null
    }
  ],
  "media_quotes": ["verbatim span that was a TV, an advert or a bystander"]
}

Use exactly these keys. Do not add fields. `due_raw`, `project` and
`supersedes` may be null. `media_quotes` is `[]` when every speaker was
present - which is the normal case.

`supersedes` is how the assistant avoids holding a stale commitment. Set it
ONLY when this item changes something agreed earlier, and describe that
earlier thing in a few words - for example "investor pitch due Friday" or
"Ravi owning the compliance forms". Leave it null otherwise.
"""


USER_TEMPLATE = """\
Conversation recorded on {recorded_on} ({weekday}) in timezone {tz}.

Speaker labels may be missing; infer names only when the transcript states them.

TRANSCRIPT
----------
{transcript}
----------

Return the JSON object now.
"""


REPAIR_TEMPLATE = """\
Your previous response could not be parsed against the required schema.

Error:
{error}

Your previous response:
{previous}

Return a corrected JSON object that satisfies the schema exactly. Same rules:
verbatim source_quote, no invented owners, due_raw copied verbatim and never
converted to a date. Return ONLY the JSON object.
"""
