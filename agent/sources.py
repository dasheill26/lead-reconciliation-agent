"""
sources.py

Loads the three data sources and — critically — decides whether each one
actually needs to be re-processed this run. In a real deployment these
would be a CRM API call, an IMAP fetch, and a scraper invocation, each
with real latency/cost; here they're files, but the change-detection logic
is exactly what you'd run against the real thing: hash the content, compare
to what we saw last run, skip if nothing moved.

This is the concrete implementation of "detect when a naive approach
(checking everything every time) would be wasteful."
"""

import csv
import json
import hashlib
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
CHECKSUM_FILE = os.path.join(STATE_DIR, "checksums.json")


def _hash_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_checksums() -> dict:
    if os.path.exists(CHECKSUM_FILE):
        with open(CHECKSUM_FILE) as f:
            return json.load(f)
    return {}


def _save_checksums(checksums: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(CHECKSUM_FILE, "w") as f:
        json.dump(checksums, f, indent=2)


def check_which_sources_changed(cost_tracker) -> dict:
    """Returns {source_name: bool} — True if changed since last run (or
    never run before). Logs a decision line for every source either way,
    so a skip is visible in the report, not silent."""
    paths = {
        "crm": os.path.join(DATA_DIR, "crm.csv"),
        "inbox": os.path.join(DATA_DIR, "inbox.json"),
        "scrape": os.path.join(DATA_DIR, "scrape.json"),
    }
    previous = _load_checksums()
    current = {name: _hash_file(path) for name, path in paths.items()}
    changed = {}
    for name in paths:
        changed[name] = previous.get(name) != current[name]
        if changed[name]:
            cost_tracker.log_decision(f"Source '{name}' changed since last run — processing.")
        else:
            cost_tracker.log_decision(
                f"Source '{name}' unchanged since last run (hash match) — skipping fetch/parse."
            )
    _save_checksums(current)
    return changed


def load_crm(cost_tracker, count_as_fetch: bool) -> dict:
    path = os.path.join(DATA_DIR, "crm.csv")
    if count_as_fetch:
        cost_tracker.record_api_call("crm_fetch")
    rows = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            row["deal_value_gbp"] = float(row["deal_value_gbp"])
            rows[row["lead_id"]] = row
    if count_as_fetch:
        cost_tracker.record_rows_touched(len(rows))
    return rows


def load_inbox(cost_tracker, count_as_fetch: bool) -> dict:
    path = os.path.join(DATA_DIR, "inbox.json")
    if count_as_fetch:
        cost_tracker.record_api_call("inbox_fetch")
    with open(path) as f:
        events = json.load(f)
    if count_as_fetch:
        cost_tracker.record_rows_touched(len(events))
    by_lead = {}
    for e in events:
        by_lead.setdefault(e["lead_id"], []).append(e)
    for lead_id in by_lead:
        by_lead[lead_id].sort(key=lambda e: e["timestamp"])
    return by_lead


def load_scrape(cost_tracker, count_as_fetch: bool) -> dict:
    path = os.path.join(DATA_DIR, "scrape.json")
    if count_as_fetch:
        cost_tracker.record_api_call("scrape_fetch")
    with open(path) as f:
        records = json.load(f)
    if count_as_fetch:
        cost_tracker.record_rows_touched(len(records))
    return {r["lead_id"]: r for r in records}
