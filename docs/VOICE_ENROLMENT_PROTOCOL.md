# Collecting clips to measure a speaker model

Roadmap step **0a**. The code is written; what is missing is audio. This is how
to record it so the measurement is worth trusting.

Nothing here changes the product. Owner voice enrolment stays inert —
`SpeakerMatch.is_owner` returns `False` whatever similarity is computed — until
a measured verdict justifies turning it on.

---

## Why the clips decide everything

Published equal-error rates for TitaNet, ECAPA-TDNN, ReDimNet2 and pyannote
are 0.1–3%, all on clean corpora like VoxCeleb. A 2026 independent comparison
found **domain shift mattered more than the gaps between models**, and a worn
device is the worst case for domain shift: distant mic, room noise, AAC
compression, cross-talk.

So the model is not chosen from a leaderboard. It is chosen by measuring
candidates on audio recorded the way the product records.

## The mistake that makes a bad model look good

**Recording all the owner clips in one sitting.** One room, one position, one
microphone warm-up, one mood — the clips then resemble each other for reasons
that have nothing to do with whose voice it is, the scores come back near 1.0,
and the model looks excellent until someone records in a car.

The guard is deliberate variety, below. If every owner clip scores above
0.95, be suspicious rather than pleased.

---

## Record through the app, not the phone's voice recorder

Use Audaxity itself. The stored segments then came through the exact audio
path the product uses — same recorder settings, same AAC encoding, same
segment length — and they are already on the laptop under
`data_phone/audio/<session>/<segment>.m4a`.

A clip recorded in a different app, at a different sample rate, measures a
pipeline you are not shipping.

### 1. Record

Short sessions in the app: start, speak for 20–40 seconds, stop. One session
per condition. **Note which is which as you go** — the transcript will not
tell you who was speaking.

**Owner clips — at least 8, deliberately varied:**

| # | Condition | Why |
|---|---|---|
| 1–2 | Quiet room, phone on the desk | the easy baseline |
| 3 | Phone in a shirt pocket | what wearing it actually sounds like |
| 4 | Noisy room — fan, traffic, a TV on | the condition that breaks models |
| 5 | Walking outdoors | wind and footfall |
| 6 | Talking across a room, 2–3 m away | distance is the usual failure |
| 7–8 | **A different day**, normal conversation | session effects are the thing to defeat |

**Impostor clips — at least 8, and the hard ones matter most:**

| # | Who | Why |
|---|---|---|
| 1–3 | People you actually talk to (Pulkit, family) | the real confusions |
| 4–5 | Someone of the same gender and similar accent | **the hardest case, and the one that decides the thresholds** |
| 6–7 | A TV, podcast or video | ties into rule 13; broadcast voices must not read as you |
| 8 | Two people talking over each other | cross-talk, which a worn device gets constantly |

**Ask before recording anyone else.** This product's entire privacy argument
is that it enrols only the owner and builds no database of other people. The
impostor clips are used to compute a false-accept rate and are then deleted;
say that plainly to whoever you record, and do not record a stranger.

### 2. Lay them out

```bash
cd backend
python scripts/collect_voice_clips.py --list
python scripts/collect_voice_clips.py --session 71255125 --as owner
python scripts/collect_voice_clips.py --session afe93a99  --as other
```

`--as` is required and never inferred. A mislabelled impostor clip makes a bad
model look good, which is the one error that would propagate silently into
every attribution the product later makes.

### 3. Measure

```bash
python scripts/measure_speaker_models.py --clips ./voice_clips
```

The first three owner clips become the enrolment centroid, exactly as the app
does it; the rest are held out, so no model is ever scored on audio it
enrolled from.

---

## Reading the verdict

The script prints EER, suggested thresholds, the uncertain band, and the worst
cases by filename so a bad recording can be listened to rather than guessed at.

| EER | Verdict | What to do |
|---|---|---|
| clean separation | every owner clip above every impostor | set the thresholds, enable the driver |
| < 5% | usable | set the thresholds, keep a wide uncertain band |
| 5–15% | marginal | usable only with a wide band; expect most clips to resolve to `unknown` |
| > 15% | not usable | try another model, or better clips, before trusting any attribution |

If the script warns that **the distributions overlap**, no threshold pair gives
both a low false-accept and a low false-reject rate. There is then no honest
uncertain band, and attribution from that model is a guess however good the
numbers look elsewhere.

### The asymmetry in the targets

`OWNER_THRESHOLD` is derived to keep false accepts under 1%; `OTHER_THRESHOLD`
to keep false rejects under 5%. That is not symmetric and should not be: a
false accept tells someone they promised something they never said, while a
false reject only leaves the owner as `unknown` — which is a correct, expected
answer everywhere else in this codebase.

---

## Turning it on

Only after a verdict justifies it:

1. Add the driver in `app/services/voice/factory.py` with a **new**
   `model_id`. Profiles record the model that produced them and refuse
   cross-model comparison, so a new id invalidates old enrolments instead of
   silently producing meaningless similarities.
2. Set its `trustworthy` to `True`. Until then `is_owner` is false by
   construction, and no code path can attribute a commitment on an unvalidated
   model's word.
3. Put the measured thresholds into `app/models/voice.py`, replacing the
   current placeholder 0.72 / 0.45.
4. Re-enrol on the device. Enrolment replaces rather than appends.

## Afterwards

**Delete the impostor clips.** They were recorded to compute a false-accept
rate, that is done, and keeping other people's voice recordings is exactly
what this product promises not to do. `*.m4a` is gitignored, so they were
never at risk of being committed — but gitignored is not deleted.
