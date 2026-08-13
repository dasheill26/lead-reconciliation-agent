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
import os
from datetime import datetime, timezone

from agent.cost_tracker import CostTracker
from agent.planner import run as run_reconciliation

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "state")


def do_run(budget_gbp: float):
    tracker = CostTracker()
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

    tracker.print_report(leads_reconciled=len(leads))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "last_run_report.json")
    with open(out_path, "w") as f:
        json.dump({"leads": leads, "cost_report": tracker.report(len(leads))}, f, indent=2)
    print(f"Full report written to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Multi-source lead reconciliation agent")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--interval", type=int, default=None,
                         help="Run every N minutes, forever (scheduled mode)")
    parser.add_argument("--budget", type=float, default=0.01,
                         help="Max £ budget for LLM escalations this run (default 0.01)")
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
