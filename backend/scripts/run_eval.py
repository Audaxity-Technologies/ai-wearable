"""Run the extraction eval set against a real LLM provider.

    python scripts/run_eval.py                 # uses LLM_PROVIDER from .env
    python scripts/run_eval.py --provider groq
    python scripts/run_eval.py --case speech_acts --verbose

Run it before and after every prompt change. It costs one LLM call per case.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make `app` importable when run as a script from the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.analysis import AnalysisService  # noqa: E402
from app.services.llm.factory import build_llm_driver  # noqa: E402
from evals.runner import (  # noqa: E402
    format_report,
    load_cases,
    run_case,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the extraction eval set")
    parser.add_argument(
        "--provider",
        choices=["groq", "gemini", "fake"],
        help="Override LLM_PROVIDER for this run",
    )
    parser.add_argument("--case", help="Run a single case by id")
    parser.add_argument(
        "--verbose", action="store_true", help="Print each transcript"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=20.0,
        help=(
            "Seconds to wait between cases. Groq's free tier allows 8000 "
            "tokens/minute and a case costs ~2000, so pacing avoids 429s."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})

    driver = build_llm_driver(settings)
    ready, detail = await driver.readiness()
    if not ready:
        print(f"LLM provider not ready: {detail}", file=sys.stderr)
        return 2

    cases = load_cases(args.case)
    if not cases:
        print("No cases found", file=sys.stderr)
        return 2

    print(f"Running {len(cases)} case(s) against {detail}\n")
    service = AnalysisService(driver=driver)

    scores = []
    for index, case in enumerate(cases):
        if index and args.delay:
            await asyncio.sleep(args.delay)
        if args.verbose:
            print(f"--- {case['id']} ---\n{case['transcript']}\n")
        print(f"  running {case['id']}...")
        scores.append(await run_case(case, service))

    print(format_report(scores))
    return 0 if all(score.passed for score in scores) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
