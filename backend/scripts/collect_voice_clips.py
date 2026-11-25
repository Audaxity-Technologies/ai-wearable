"""Lay out recorded segments as labelled clips for speaker measurement.

    python scripts/collect_voice_clips.py --list
    python scripts/collect_voice_clips.py --session 71255125 --as owner
    python scripts/collect_voice_clips.py --session afe93a99 --as other

## Why go through the app rather than copying files off the phone

The measurement is only worth anything if the clips came through the **same
audio path as the product**: the same recorder settings, the same AAC
encoding, the same segment length. A speaker embedding is sensitive to codec
and channel, which is exactly the domain shift that makes published EER
figures misleading here.

Recording a few short sessions in the app and pulling the stored segments
guarantees that path for free - and the files are already on the laptop,
under `DATA_DIR/audio/<session_id>/<segment_id>.m4a`.

## It will not label them for you

Which voice is in a recording is something only you know, so `--as` is
required and there is no inference from the transcript. Guessing here would
poison the measurement in the one direction that matters: an impostor clip
mislabelled as the owner makes a bad model look good.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

DEFAULT_DATA = Path("data_phone")
DEFAULT_OUT = Path("voice_clips")
LABELS = ("owner", "other")


def load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def list_sessions(data_dir: Path) -> int:
    sessions = load(data_dir / "store" / "sessions.json")
    segments = load(data_dir / "store" / "segments.json")
    if not sessions:
        print(f"No sessions in {data_dir / 'store' / 'sessions.json'}")
        return 1

    by_session: dict[str, list[dict]] = {}
    for segment in segments.values():
        by_session.setdefault(segment["session_id"], []).append(segment)

    rows = sorted(
        sessions.values(), key=lambda s: s.get("started_at", ""), reverse=True
    )
    print(f"{'session':10}  {'when':17}  {'segs':>4}  {'secs':>6}  transcript")
    print("-" * 100)
    for session in rows:
        own = by_session.get(session["id"], [])
        seconds = sum(segment.get("duration_s", 0) for segment in own)
        text = (session.get("transcript") or "").replace("\n", " ").strip()
        print(
            f"{session['id'][:8]:10}  "
            f"{session.get('started_at', '')[:16]:17}  "
            f"{len(own):4d}  {seconds:6.0f}  "
            f"{text[:52]!r}"
        )
    print()
    print("Pick the ones where you know who was speaking, then:")
    print("  --session <first 8 chars> --as owner|other")
    return 0


def copy_clips(
    data_dir: Path, out_dir: Path, prefix: str, label: str
) -> int:
    segments = load(data_dir / "store" / "segments.json")
    sessions = load(data_dir / "store" / "sessions.json")

    matches = [sid for sid in sessions if sid.startswith(prefix)]
    if not matches:
        print(f"No session starting with {prefix!r}.", file=sys.stderr)
        return 2
    if len(matches) > 1:
        print(
            f"{prefix!r} matches {len(matches)} sessions; use more characters.",
            file=sys.stderr,
        )
        return 2
    session_id = matches[0]

    mine = sorted(
        (s for s in segments.values() if s["session_id"] == session_id),
        key=lambda s: s.get("seq", 0),
    )
    if not mine:
        print(f"Session {session_id[:8]} has no segments.", file=sys.stderr)
        return 2

    target = out_dir / label
    target.mkdir(parents=True, exist_ok=True)

    copied = skipped = 0
    for segment in mine:
        source = data_dir / "audio" / session_id / Path(segment["blob_key"]).name
        if not source.is_file():
            # The blob key is the authority, but a store edited by hand can
            # point at a file that is gone. Say so rather than silently
            # producing a smaller clip set than the operator expects.
            print(f"  missing: {source}", file=sys.stderr)
            skipped += 1
            continue
        # Encode the condition into the name so `measure_speaker_models.py`'s
        # "worst cases" output names something you can actually go and listen
        # to.
        name = f"{label}-{session_id[:8]}-{segment.get('seq', 0):02d}{source.suffix}"
        shutil.copy2(source, target / name)
        copied += 1

    print(f"copied {copied} clip(s) to {target}")
    if skipped:
        print(f"skipped {skipped} missing blob(s)")

    total = len(list(target.glob("*")))
    print(f"{target} now holds {total} clip(s)")
    print()
    print("When you have 8+ in each of owner/ and other/:")
    print(f"  python scripts/measure_speaker_models.py --clips {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lay out recorded segments as labelled speaker clips"
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--list", action="store_true", help="List sessions")
    parser.add_argument("--session", help="Session id, or its first characters")
    parser.add_argument(
        "--as",
        dest="label",
        choices=LABELS,
        help="Who is speaking in this session. Required, never inferred.",
    )
    args = parser.parse_args()

    if args.list or not args.session:
        return list_sessions(args.data)
    if not args.label:
        print(
            "--as owner|other is required: which voice is in a recording is "
            "something only you know, and a mislabelled impostor clip makes a "
            "bad model look good.",
            file=sys.stderr,
        )
        return 2
    return copy_clips(args.data, args.out, args.session, args.label)


if __name__ == "__main__":
    raise SystemExit(main())
