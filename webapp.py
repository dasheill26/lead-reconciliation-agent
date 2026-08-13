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

import os
from flask import Flask, render_template

from agent.cost_tracker import CostTracker
from agent.planner import run as run_reconciliation

app = Flask(__name__)

# Public demo: never touch a real API key even if one happens to be set
# in the environment this runs in.
os.environ.pop("ANTHROPIC_API_KEY", None)


@app.route("/")
def dashboard():
    tracker = CostTracker()
    leads = run_reconciliation(tracker, budget_gbp=0.01)
    conflicts = [l for l in leads if l.get("conflict_type")]
    report = tracker.report(leads_reconciled=len(leads))
    return render_template(
        "dashboard.html",
        leads=leads,
        conflicts_count=len(conflicts),
        report=report,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
