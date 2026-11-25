"""Rebuild the utterance index from stored segments.

    python scripts/rebuild_utterances.py              # every session
    python scripts/rebuild_utterances.py --session 71255125

The index is derived, so this is always safe to run: it deletes a session's
rows and rebuilds them from the segments and the stored analysis. That
property is the point of phase C - the linking rules will change as phase D
learns what questions actually need, and a layer you cannot rebuild is a
layer you cannot fix.

Use it to backfill conversations recorded before the index existed, and after
editing `LINK_THRESHOLD` or the linking rules in `services/utterances.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.container import build_container  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the utterance index")
    parser.add_argument("--session", help="Session id, or its first characters")
    parser.add_argument(
        "--verbose", action="store_true", help="Print each utterance"
    )
    args = parser.parse_args()

    container = build_container(get_settings())
    sessions = await container.sessions_repo.list()
    if args.session:
        sessions = [s for s in sessions if s.id.startswith(args.session)]
        if not sessions:
            print(f"No session starting with {args.session!r}", file=sys.stderr)
            return 2

    total = linked = placed = 0
    for session in sorted(sessions, key=lambda s: s.started_at):
        segments = await container.segments_repo.list(session_id=session.id)
        if not segments:
            continue
        analysis = await container.analyses_repo.get(session.id)
        built = await container.utterance_service.rebuild_for_session(
            session, segments, analysis
        )
        total += len(built)
        linked += sum(1 for u in built if u.act is not None)
        placed += sum(1 for u in built if u.place is not None)

        print(
            f"{session.id[:8]}  {session.started_at:%Y-%m-%d %H:%M}  "
            f"{len(built):3d} utterances  "
            f"{sum(1 for u in built if u.act is not None):2d} linked"
            + ("  (no analysis yet)" if analysis is None else "")
        )
        if args.verbose:
            for utterance in built:
                flag = f"[{utterance.act.value}]" if utterance.act else ""
                where = (
                    f" @{utterance.place.describe()}" if utterance.place else ""
                )
                print(
                    f"     {utterance.started_at:%H:%M:%S} "
                    f"{utterance.duration_s:5.1f}s{where} {flag} "
                    f"{utterance.text[:60]!r}"
                )

    print()
    print(f"{total} utterance(s); {linked} carry a speech act; "
          f"{placed} carry a place")
    if total and not linked:
        print("No links at all - check that analyses exist for these sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
