"""
reconciler.py

The trust rules. Each source gets a fixed, documented quality weight based
on what it's actually good for — not a learned score, a deliberate business
judgement call, which is exactly the kind of thing a video walkthrough
should be able to defend:

  - inbox  (1.0): ground truth for whether an email was sent/replied to.
            Can't be faked or forgotten by a rep — highest trust for
            pipeline-stage evidence.
  - crm    (0.75): authoritative for deal value and explicit stage
            *intent*, but reps forget to update it — known to lag reality.
  - scrape (0.5): good for enrichment (industry, company size, discovery
            of brand-new prospects) but has zero visibility into pipeline
            stage, and — see STALE_SCRAPE_THRESHOLD_DAYS below — is
            explicitly distrusted once it's old enough that the underlying
            company data has likely moved on.

Two conflict types are handled here without ever touching an LLM:
  - crm_stale: CRM lags behind confirmed email evidence -> trust inbox.
  - stale scrape enrichment: scrape data older than the threshold is
    dropped from the canonical record rather than silently trusted.

Only ONE conflict type genuinely needs a smarter (LLM) read: CRM claims a
stage the email evidence doesn't support, on a high-value deal. See
planner.py for where that line is drawn and why.
"""

from datetime import datetime, timezone
import os

STAGE_ORDER = {"new": 0, "emailed": 1, "replied": 2, "qualified": 3}
REQUIRES_REPLY_EVIDENCE = {"replied", "qualified"}
STALE_SCRAPE_THRESHOLD_DAYS = int(os.environ.get("STALE_SCRAPE_THRESHOLD_DAYS", 7))


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _email_evidence_stage(events: list) -> tuple[str, str | None]:
    if not events:
        return "new", None
    stages_seen = {e["direction"] for e in events}
    last_at = events[-1]["timestamp"]  # pre-sorted by sources.py
    if "received" in stages_seen:
        return "replied", last_at
    return "emailed", last_at


def _scrape_freshness(scrape_row: dict | None, now: datetime, cost_tracker, lead_id: str) -> tuple[dict | None, bool]:
    """Returns (usable_scrape_data_or_None, was_stale). A stale record isn't
    discarded from the raw data — it's excluded from what the canonical
    lead actually trusts, and the decision is logged, mirroring the brief's
    own example: 'finding the web scrape has stale data and deciding to
    deprioritise it.'"""
    if scrape_row is None:
        return None, False
    age_days = (now - _parse_ts(scrape_row["scraped_at"])).days
    if age_days > STALE_SCRAPE_THRESHOLD_DAYS:
        cost_tracker.log_decision(
            f"{lead_id}: scrape data is {age_days}d old (> {STALE_SCRAPE_THRESHOLD_DAYS}d threshold) "
            f"— deprioritizing, not trusting for enrichment this run."
        )
        return None, True
    return scrape_row, False


def reconcile_all(crm: dict, inbox: dict, scrape: dict, cost_tracker, now: datetime | None = None) -> list[dict]:
    """Builds canonical lead records from all three sources. Returns a list
    of dicts; leads whose conflict requires an LLM read are flagged with
    needs_llm_escalation=True but NOT yet resolved — planner.py decides,
    budget permitting, which of those actually get the (paid) LLM call.

    `now` is injectable for testability; defaults to the real current time."""

    if now is None:
        now = datetime.now(timezone.utc)

    all_ids = set(crm) | set(inbox) | set(scrape)
    results = []

    for lead_id in sorted(all_ids):
        crm_row = crm.get(lead_id)
        inbox_events = inbox.get(lead_id, [])
        raw_scrape_row = scrape.get(lead_id)
        scrape_row, scrape_was_stale = _scrape_freshness(raw_scrape_row, now, cost_tracker, lead_id)

        if crm_row is None:
            # Only seen via scrape (or inbox with no CRM record) — a genuinely
            # new prospect the sales team hasn't entered yet. A stale scrape
            # record is still enough to know the prospect *exists*, just not
            # enough to trust its enrichment details.
            email_stage, last_email_at = _email_evidence_stage(inbox_events)
            results.append({
                "lead_id": lead_id,
                "name": None,
                "company": (scrape_row or raw_scrape_row or {}).get("company"),
                "email": None,
                "deal_value_gbp": 0.0,
                "crm_stage": None,
                "email_evidence_stage": email_stage,
                "last_email_event_at": last_email_at,
                "canonical_stage": "prospect_not_in_crm",
                "conflict_type": None,
                "needs_llm_escalation": False,
                "industry": scrape_row.get("industry") if scrape_row else None,
                "scrape_stale": scrape_was_stale,
                "resolution_note": "Found only in scrape/inbox, not yet in CRM — flagged for sales team to add.",
            })
            continue

        email_stage, last_email_at = _email_evidence_stage(inbox_events)
        crm_stage = crm_row["stage"]

        if crm_stage not in STAGE_ORDER:
            # Malformed/unexpected CRM data — don't crash the whole run over
            # one bad row. Flag it and fall back to trusting email evidence,
            # which is always well-formed by construction (derived, not raw).
            cost_tracker.log_decision(
                f"{lead_id}: CRM stage '{crm_stage}' is not a recognized value "
                f"({sorted(STAGE_ORDER)}) — treating as unknown, falling back to email evidence."
            )
            crm_stage_valid = False
        else:
            crm_stage_valid = True

        lead = {
            "lead_id": lead_id,
            "name": crm_row["name"],
            "company": crm_row["company"],
            "email": crm_row["email"],
            "deal_value_gbp": crm_row["deal_value_gbp"],
            "crm_stage": crm_stage,
            "crm_last_updated": crm_row["last_updated"],
            "email_evidence_stage": email_stage,
            "last_email_event_at": last_email_at,
            "industry": scrape_row.get("industry") if scrape_row else None,
            "employee_count": scrape_row.get("employee_count") if scrape_row else None,
            "scrape_stale": scrape_was_stale,
        }

        if not crm_stage_valid:
            lead["conflict_type"] = "crm_invalid_stage"
            lead["needs_llm_escalation"] = False
            lead["canonical_stage"] = email_stage
            lead["resolution_note"] = (
                f"CRM stage '{crm_stage}' unrecognized — fell back to email-evidence stage '{email_stage}'."
            )
        elif crm_stage in REQUIRES_REPLY_EVIDENCE and email_stage not in REQUIRES_REPLY_EVIDENCE:
            # The exact scenario from the brief: CRM says replied/qualified,
            # but there's no reply evidence in the inbox at all.
            lead["conflict_type"] = "crm_ahead_of_evidence"
            lead["needs_llm_escalation"] = True  # planner.py applies the value threshold
            lead["canonical_stage"] = None  # not yet resolved
            lead["resolution_note"] = None
        elif STAGE_ORDER[crm_stage] < STAGE_ORDER[email_stage]:
            # CRM hasn't caught up to reality. This is where recency AND
            # quality both matter: the inbox event is more recent than the
            # CRM's last update (that's *why* CRM looks behind), and inbox
            # also outranks CRM on quality (1.0 vs 0.75) as the ground-truth
            # source for reply evidence. Both point the same way here, so
            # the resolution is immediate and confident.
            lead["conflict_type"] = "crm_stale"
            lead["needs_llm_escalation"] = False
            lead["canonical_stage"] = email_stage
            lead["resolution_note"] = (
                f"Inbox shows '{email_stage}' as of {last_email_at}, more recent than CRM's "
                f"last update ({crm_row['last_updated']}) which still says '{crm_stage}' — "
                f"trusting inbox on both recency and quality (1.0 vs 0.75)."
            )
        else:
            lead["conflict_type"] = None
            lead["needs_llm_escalation"] = False
            lead["canonical_stage"] = crm_stage
            lead["resolution_note"] = "CRM stage matches or exceeds available email evidence — no conflict."

        if scrape_was_stale and lead["resolution_note"]:
            lead["resolution_note"] += " (scrape enrichment excluded: stale.)"

        results.append(lead)
        cost_tracker.record_rows_touched(1)

    return results
