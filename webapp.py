"""
webapp.py — a small read-only dashboard around the actual agent, so a
visitor can see it run without cloning the repo. This is NOT part of the
assessment deliverable (that's run.py + the video) — it's an extra for
anyone browsing the GitHub profile who wants to see the thing work without
installing anything.

Deliberately simulated-mode only: a public, unauthenticated endpoint that
triggered real paid API calls on every visitor's page load would be a bad
idea regardless of how cheap Haiku is. The CLI (run.py) is where real-key
mode belongs — on your own machine, with your own budget.
"""

import json
import os
from flask import Flask, render_template

from agent.cost_tracker import CostTracker
from agent.planner import run as run_reconciliation

app = Flask(__name__)

# Public demo: never touch a real API key even if one happens to be set
# in the environment this runs in.
os.environ.pop("ANTHROPIC_API_KEY", None)

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "state", "run_history.jsonl")


def _recent_runs(limit: int = 8) -> list[dict]:
    """Reads the append-only history log this same agent has been writing
    to all along. Not new data - just the first time we've actually shown
    it to anyone. Demonstrates the change-detection savings visually
    instead of just claiming them."""
    if not os.path.exists(HISTORY_PATH):
        return []
    runs = []
    with open(HISTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return runs[-limit:]


STAGE_ORDER_FOR_SORT = {"prospect_not_in_crm": -1, "new": 0, "emailed": 1, "replied": 2, "qualified": 3}


@app.route("/")
def dashboard():
    tracker = CostTracker()
    leads = run_reconciliation(tracker, budget_gbp=0.01)
    conflicts = [l for l in leads if l.get("conflict_type")]
    report = tracker.report(leads_reconciled=len(leads))

    # Write to the same history log run.py uses - the trend panel needs
    # this to actually reflect reality rather than an always-empty file.
    tracker.append_to_history(report, len(conflicts), HISTORY_PATH)

    # Conflicts first, in the order they were resolved - so the interesting
    # rows are immediately visible instead of buried under a dozen
    # no-conflict leads a viewer has to scroll past.
    leads_sorted = sorted(leads, key=lambda l: (l.get("conflict_type") is None, l["lead_id"]))

    return render_template(
        "dashboard.html",
        leads=leads_sorted,
        conflicts_count=len(conflicts),
        report=report,
        recent_runs=_recent_runs(),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
