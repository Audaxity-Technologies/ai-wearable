"""Run the retrieval eval set (memory phase G).

    python scripts/run_question_eval.py
    python scripts/run_question_eval.py --case time_and_place_and_act

Costs nothing and cannot flake: phase D is deterministic, so there is no LLM
call. It also runs inside the ordinary test suite - see
`tests/test_question_evals.py` - and this script exists for the readable
report while iterating on the parser.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.question_runner import (  # noqa: E402
    build_world,
    format_report,
    load_cases,
    run_case,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the retrieval eval set")
    parser.add_argument("--case", help="Run a single case by id")
    parser.add_argument(
        "--plans", action="store_true", help="Print each resolved filter"
    )
    args = parser.parse_args()

    cases = load_cases()["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            print(f"No case named {args.case!r}", file=sys.stderr)
            return 2

    with tempfile.TemporaryDirectory() as raw:
        question_parser, user, now = await build_world(Path(raw))
        scores = []
        for case in cases:
            if args.plans:
                resolved = await question_parser.parse(
                    case["question"], user_id=user, tz="Asia/Kolkata", now=now
                )
                print(f"  {case['id']:26} {resolved.kind.value:13} "
                      f"| {resolved.describe()}")
            scores.append(await run_case(case, question_parser, user, now))

    print(format_report(scores))
    return 0 if all(score.passed for score in scores) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
