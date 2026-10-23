"""Run local versioned regression experiments; never dispatch paid calls implicitly."""

import argparse
import json
from app.evaluation.runner import run_offline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--dataset", default="evaluation/cases.jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--model")
    parser.add_argument("--user-id")
    parser.add_argument("--max-cost-usd")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.mode == "live":
        if not all((args.model, args.user_id, args.max_cost_usd)):
            parser.error("live mode requires --model, --user-id and --max-cost-usd")
        import asyncio
        from app.evaluation.live import run_live

        report = asyncio.run(
            run_live(
                args.dataset,
                args.output,
                args.model,
                args.user_id,
                args.max_cost_usd,
                args.repetitions,
            )
        )
    else:
        report = run_offline(args.dataset, args.output, args.baseline)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "cases": len(report["cases"]),
                "categories": report.get("categories", {}),
            }
        )
    )
    return (
        0
        if report["passed"] and report.get("comparison", {}).get("passed", True)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
