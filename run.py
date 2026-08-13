#!/usr/bin/env python3
"""
run.py — CLI entrypoint for the lead reconciliation agent.

Usage:
    python run.py --once                  # single run, prints report, exits
    python run.py --once --budget 0.0005  # force the budget-cap path (demo)
    python run.py --interval 15           # runs every 15 minutes, forever

--once is what you want for the demo video: a single pass, full decision
log, full cost report, done in a couple of seconds.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()  # populates os.environ from .env if present - must run before agent modules read it

from agent.cost_tracker import CostTracker
from agent.planner import run as run_reconciliation

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "state")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger("lead_reconciliation_agent")


def do_run(budget_gbp: float):
    tracker = CostTracker()
    logger.info(f"Starting reconciliation run (budget=£{budget_gbp})")
    print(f"Starting reconciliation run at {datetime.now(timezone.utc).isoformat()}...")
    leads = run_reconciliation(tracker, budget_gbp=budget_gbp)

    conflicts = [l for l in leads if l.get("conflict_type")]
    print(f"\nReconciled {len(leads)} leads ({len(conflicts)} had a conflict to resolve).\n")

    print(f"{'ID':<6}{'Name':<16}{'CRM Stage':<12}{'Canonical Stage':<20}{'Note'}")
    print("-" * 100)
    for l in leads:
        name = (l["name"] or "—")[:15]
        crm_stage = (l["crm_stage"] or "—")[:11]
        canonical = (l["canonical_stage"] or "—")[:19]
        note = (l["resolution_note"] or "")[:55]
        print(f"{l['lead_id']:<6}{name:<16}{crm_stage:<12}{canonical:<20}{note}")

    # Computed exactly once, so the printed report, the JSON snapshot, and
    # the history log line can never disagree with each other on numbers
    # like compute_time_seconds that change with every call.
    cost_report = tracker.report(leads_reconciled=len(leads))
    tracker.print_report(cost_report)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report = {"leads": leads, "cost_report": cost_report}

    # Snapshot of the most recent run - easy to inspect/diff.
    out_path = os.path.join(OUTPUT_DIR, "last_run_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    # Append-only audit trail across every run - what a real deployment
    # needs for monitoring spend over time, not just the latest snapshot.
    # Shared method also used by webapp.py, so both surfaces write the
    # same file consistently.
    history_path = os.path.join(OUTPUT_DIR, "run_history.jsonl")
    tracker.append_to_history(cost_report, len(conflicts), history_path)

    logger.info(f"Run complete: {len(leads)} leads, {len(conflicts)} conflicts, "
                f"£{tracker.llm_inference_cost_gbp:.4f} inference cost")
    print(f"Full report written to {out_path}")
    print(f"Run appended to history log: {history_path}")


def main():
    parser = argparse.ArgumentParser(description="Multi-source lead reconciliation agent")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--interval", type=int, default=None,
                         help="Run every N minutes, forever (scheduled mode)")
    parser.add_argument("--budget", type=float, default=0.01,
                         help="Max £ budget for LLM escalations this run (default 0.01)")
    args = parser.parse_args()

    try:
        if args.interval:
            import schedule
            import time
            print(f"Scheduling reconciliation every {args.interval} minute(s). Ctrl+C to stop.")
            schedule.every(args.interval).minutes.do(do_run, budget_gbp=args.budget)
            do_run(budget_gbp=args.budget)  # run once immediately too
            while True:
                schedule.run_pending()
                time.sleep(1)
        else:
            # --once is also the default if neither flag is given
            do_run(budget_gbp=args.budget)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
    except Exception as e:
        logger.exception("Reconciliation run failed")
        print(f"\nRun failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
