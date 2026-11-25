"""Measure a speaker-embedding model on real recordings from the device.

    python scripts/measure_speaker_models.py --clips ./voice_clips
    python scripts/measure_speaker_models.py --clips ./voice_clips --driver fake

## Why this script exists

`app/services/voice/factory.py` ships only the fake driver, deliberately. The
published equal-error rates for TitaNet, ECAPA-TDNN, ReDimNet2 and pyannote
are all measured on clean corpora, and an independent 2026 comparison found
domain shift mattered more than the gaps between them. Wearable audio is the
worst case for domain shift: distant mic, room noise, lossy codec, cross-talk.

Choosing from a leaderboard would therefore be choosing in the dark. This
turns the choice into a measurement on the audio the product will actually
see.

## What it reports, and why each number matters

- **EER** - the error rate where false accepts equal false rejects. The single
  comparable number between models.
- **The two thresholds**, derived from the measured distributions rather than
  from a paper. `OWNER_THRESHOLD` is set so false accepts are rare, because a
  false "this was you" attributes someone else's promise to the user, which is
  the costliest error in the product. `OTHER_THRESHOLD` is set so false
  rejects are rare.
- **The uncertain band** those two thresholds leave, as a fraction of pairs.
  A band that swallows everything means the model cannot separate this
  audio; a band of zero means the thresholds are not conservative enough to
  be worth having.
- **Worst cases** - the owner clips that scored lowest and the impostor clips
  that scored highest, by filename, so a bad recording can be inspected
  rather than guessed at.

## Clip layout

    voice_clips/
      owner/   me-quiet-01.m4a  me-noisy-02.m4a  ...   (>= 8)
      other/   pulkit-01.m4a    tv-01.m4a        ...   (>= 8)

See `docs/VOICE_ENROLMENT_PROTOCOL.md` for how to record them. Enrolment uses
the first few owner clips; everything else is held out for scoring, so the
model is never evaluated on the audio it enrolled from.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.voice import (  # noqa: E402
    MIN_ENROLMENT_CLIPS,
    OTHER_THRESHOLD,
    OWNER_THRESHOLD,
)
from app.services.voice.base import SpeakerEmbeddingDriver  # noqa: E402
from app.services.voice.enrolment import cosine  # noqa: E402
from app.services.voice.fake_driver import (  # noqa: E402
    FakeSpeakerEmbeddingDriver,
)

AUDIO_SUFFIXES = {".m4a", ".wav", ".mp3", ".ogg", ".flac"}

#: How many owner clips go into the enrolment centroid. The rest are scored.
#: Matches the app's own enrolment, so the measurement reflects what users get.
ENROL_CLIPS = MIN_ENROLMENT_CLIPS

#: Target false-accept rate when deriving OWNER_THRESHOLD. Deliberately
#: strict: a false accept tells someone they promised something they did not.
TARGET_FALSE_ACCEPT = 0.01

#: Target false-reject rate when deriving OTHER_THRESHOLD. Looser, because
#: the cost of a false reject is only that the owner stays "unknown" - which
#: is a correct, expected answer everywhere in this codebase.
TARGET_FALSE_REJECT = 0.05


@dataclass
class Scored:
    name: str
    similarity: float


def collect(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )


def centroid(embeddings: list[list[float]]) -> list[float]:
    dim = len(embeddings[0])
    summed = [0.0] * dim
    for embedding in embeddings:
        for index, value in enumerate(embedding):
            summed[index] += value
    norm = sum(value * value for value in summed) ** 0.5
    if norm == 0:
        raise SystemExit("Enrolment clips cancelled out; re-record them.")
    return [value / norm for value in summed]


def equal_error_rate(
    owner: list[float], impostor: list[float]
) -> tuple[float, float]:
    """EER and the threshold at which it occurs.

    Swept over every observed score rather than a fixed grid, so the result
    does not depend on a step size chosen by whoever wrote this.
    """
    candidates = sorted({*owner, *impostor})
    best = (1.0, 0.0)
    for threshold in candidates:
        false_reject = sum(1 for value in owner if value < threshold) / len(owner)
        false_accept = sum(
            1 for value in impostor if value >= threshold
        ) / len(impostor)
        gap = abs(false_reject - false_accept)
        if gap < best[0]:
            best = (gap, threshold)
            eer = (false_reject + false_accept) / 2
    threshold = best[1]
    false_reject = sum(1 for value in owner if value < threshold) / len(owner)
    false_accept = sum(1 for value in impostor if value >= threshold) / len(
        impostor
    )
    return (false_reject + false_accept) / 2, threshold


def other_threshold_from(impostor: list[float], target: float) -> float:
    """Below this, confidently NOT the owner.

    Derived from the impostor distribution: sits just above the highest
    impostor score we are willing to let through, so at most `target` of
    impostors score at or above it.
    """
    ordered = sorted(impostor, reverse=True)
    allowed = int(len(ordered) * target)
    return ordered[allowed] + 1e-6 if allowed < len(ordered) else 0.0


def owner_threshold_from(owner: list[float], target: float) -> float:
    """At or above this, confidently the owner.

    Derived from the owner distribution: sits just below the lowest genuine
    owner score we are willing to reject, so at most `target` of owner clips
    fall below it.

    Note which distribution each threshold comes from. An earlier version of
    this script had them swapped, which on cleanly separated data produced
    `0.094 / 0.094` - an owner threshold hugging the impostor scores and no
    uncertain band at all, meaning an ambiguous clip at 0.5 would have been
    called "confidently you". Running the harness caught it; reading it did
    not.
    """
    ordered = sorted(owner)
    allowed = int(len(ordered) * target)
    return ordered[allowed] - 1e-6 if allowed < len(ordered) else 1.0


def build_driver(name: str) -> SpeakerEmbeddingDriver:
    if name == "fake":
        # `trustworthy=True` only so the harness exercises the same code path
        # a real driver would. It measures nothing about real audio.
        return FakeSpeakerEmbeddingDriver(trustworthy=True)
    raise SystemExit(
        f"No driver named {name!r} is wired up.\n"
        "Only 'fake' ships today - see app/services/voice/factory.py for why,\n"
        "and add a real driver there before measuring."
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure a speaker model on real device recordings"
    )
    parser.add_argument(
        "--clips",
        type=Path,
        required=True,
        help="Folder containing owner/ and other/ subfolders",
    )
    parser.add_argument("--driver", default="fake")
    parser.add_argument(
        "--verbose", action="store_true", help="Print every pair's score"
    )
    args = parser.parse_args()

    owner_clips = collect(args.clips / "owner")
    other_clips = collect(args.clips / "other")

    if len(owner_clips) < ENROL_CLIPS + 2:
        print(
            f"Need at least {ENROL_CLIPS + 2} clips in {args.clips / 'owner'} "
            f"({ENROL_CLIPS} to enrol from, the rest to score); "
            f"found {len(owner_clips)}.",
            file=sys.stderr,
        )
        return 2
    if not other_clips:
        print(
            f"Need clips in {args.clips / 'other'} - without impostors there "
            "is no false-accept rate, and the measurement is meaningless.",
            file=sys.stderr,
        )
        return 2

    driver = build_driver(args.driver)
    ready, detail = await driver.readiness()
    if not ready:
        print(f"Driver not ready: {detail}", file=sys.stderr)
        return 2

    print(f"driver        {driver.model_id}  ({detail})")
    print(f"trustworthy   {driver.trustworthy}")
    print(f"enrolling on  {ENROL_CLIPS} clips, holding out the rest\n")

    async def embed(path: Path) -> list[float]:
        return await driver.embed(
            path.read_bytes(), mime_type=f"audio/{path.suffix.lstrip('.')}"
        )

    enrol_set = owner_clips[:ENROL_CLIPS]
    held_out = owner_clips[ENROL_CLIPS:]

    profile = centroid([await embed(path) for path in enrol_set])

    owner_scored = [
        Scored(path.name, cosine(profile, await embed(path)))
        for path in held_out
    ]
    other_scored = [
        Scored(path.name, cosine(profile, await embed(path)))
        for path in other_clips
    ]

    owner_scores = [entry.similarity for entry in owner_scored]
    other_scores = [entry.similarity for entry in other_scored]

    if args.verbose:
        for entry in (*owner_scored, *other_scored):
            print(f"  {entry.similarity:+.4f}  {entry.name}")
        print()

    eer, eer_threshold = equal_error_rate(owner_scores, other_scores)

    suggested_owner = owner_threshold_from(owner_scores, TARGET_FALSE_REJECT)
    suggested_other = other_threshold_from(other_scores, TARGET_FALSE_ACCEPT)

    inverted = suggested_other > suggested_owner
    if inverted:
        # The distributions overlap so badly that no conservative pair exists:
        # any threshold low enough to accept the owner also accepts
        # impostors. Collapsing to the EER point is the honest fallback, and
        # it means there is no uncertain band to hide behind.
        suggested_owner = suggested_other = eer_threshold

    uncertain = sum(
        1
        for value in (*owner_scores, *other_scores)
        if suggested_other <= value < suggested_owner
    ) / (len(owner_scores) + len(other_scores))

    print("--- separation ---")
    print(
        f"  owner    n={len(owner_scores):3d}  "
        f"mean {statistics.fmean(owner_scores):+.4f}  "
        f"min {min(owner_scores):+.4f}"
    )
    print(
        f"  impostor n={len(other_scores):3d}  "
        f"mean {statistics.fmean(other_scores):+.4f}  "
        f"max {max(other_scores):+.4f}"
    )
    print(f"  margin   {min(owner_scores) - max(other_scores):+.4f}")
    print()
    print("--- error rates ---")
    print(f"  EER            {eer * 100:.2f}%  at threshold {eer_threshold:.4f}")
    print()
    print("--- thresholds to put in app/models/voice.py ---")
    print(f"  OWNER_THRESHOLD = {suggested_owner:.3f}   "
          f"(from owner scores; <= {TARGET_FALSE_REJECT:.0%} false rejects)")
    print(f"  OTHER_THRESHOLD = {suggested_other:.3f}   "
          f"(from impostor scores; <= {TARGET_FALSE_ACCEPT:.0%} false accepts)")
    print(f"  currently        {OWNER_THRESHOLD} / {OTHER_THRESHOLD}")
    print(f"  uncertain band   {uncertain:.0%} of clips land in it")
    if inverted:
        print("  WARNING  the distributions overlap: no threshold pair gives "
              "both a low")
        print("           false-accept and a low false-reject rate, so there "
              "is no honest")
        print("           uncertain band. Treat any attribution from this "
              "model as a guess.")
    print()

    worst_owner = sorted(owner_scored, key=lambda e: e.similarity)[:3]
    best_impostor = sorted(
        other_scored, key=lambda e: e.similarity, reverse=True
    )[:3]
    print("--- worst cases, to listen to ---")
    for entry in worst_owner:
        print(f"  owner scored low   {entry.similarity:+.4f}  {entry.name}")
    for entry in best_impostor:
        print(f"  impostor scored high {entry.similarity:+.4f}  {entry.name}")
    print()

    # A verdict, in the terms the product cares about.
    if min(owner_scores) > max(other_scores):
        print("VERDICT  cleanly separable on this audio: every owner clip "
              "scored above every impostor.")
    elif eer < 0.05:
        print(f"VERDICT  usable: EER {eer:.1%}. Set the thresholds above and "
              "keep the uncertain band.")
    elif eer < 0.15:
        print(f"VERDICT  marginal: EER {eer:.1%}. Usable only with a wide "
              "uncertain band; expect most clips to resolve to 'unknown'.")
    else:
        print(f"VERDICT  not usable on this audio: EER {eer:.1%}. Try another "
              "model, or better clips, before trusting any attribution.")
    print()
    print("Nothing here changes the product. Edit the thresholds and set the "
          "driver's `trustworthy` to True only once a verdict justifies it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
