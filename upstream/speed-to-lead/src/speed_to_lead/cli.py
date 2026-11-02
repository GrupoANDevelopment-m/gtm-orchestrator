"""Command-line entry point: `speed-to-lead {demo,serve}`."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .agents import build_pipeline
from .analytics import get_metrics
from .config import get_settings
from .logging import configure_logging
from .models import InboundLead
from .normalize import normalize_lead

_SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample_leads.json"


async def _run_demo(path: Path) -> None:
    settings = get_settings()
    configure_logging("WARNING", pretty=True)  # quiet logs; we print our own table
    pipeline = build_pipeline(settings)
    metrics = get_metrics()

    leads = [InboundLead.model_validate(row) for row in json.loads(path.read_text())]
    print(f"\n  speed-to-lead-agent · demo mode={settings.demo_mode} · {len(leads)} leads\n")
    print(f"  {'TIER':<6}{'SCORE':<7}{'INTENT':<18}{'LATENCY':<9}{'EMAIL'}")
    print("  " + "─" * 70)
    for inbound in leads:
        outcome = await pipeline.run(normalize_lead(inbound))
        metrics.record(outcome)
        q = outcome.qualification
        print(
            f"  {q.tier.value.upper():<6}{q.score:<7.2f}{q.intent:<18}"
            f"{outcome.latency_ms:<9.1f}{outcome.lead.email}"
        )

    snap = metrics.snapshot()
    print("\n  Funnel:")
    print(f"    qualification rate : {snap['qualification_rate']:.0%}")
    print(f"    by tier            : {snap['by_tier']}")
    print(f"    by source (attrib) : {snap['by_source']}")
    print(f"    response p50 / p95 : {snap['response_ms_p50']} / {snap['response_ms_p95']} ms\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="speed-to-lead")
    sub = parser.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("demo", help="run sample leads through the pipeline (keyless)")
    demo.add_argument("--file", type=Path, default=_SAMPLE)
    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    if args.cmd == "demo":
        asyncio.run(_run_demo(args.file))
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("speed_to_lead.api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
