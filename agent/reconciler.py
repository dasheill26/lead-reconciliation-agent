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
            stage and can be stale by design (scraped periodically, not
            live).

Only ONE conflict type genuinely needs a smarter (LLM) read: CRM claims a
stage the email evidence doesn't support, on a high-value deal. Everything
else is resolved by fast, cheap, deterministic rules — see planner.py for
where the line is drawn and why.
"""

from datetime import datetime, timezone

STAGE_ORDER = {"new": 0, "emailed": 1, "replied": 2, "qualified": 3}
REQUIRES_REPLY_EVIDENCE = {"replied", "qualified"}


def _email_evidence_stage(events: list) -> tuple[str, str | None]:
    if not events:
        return "new", None
    stages_seen = {e["direction"] for e in events}
    last_at = events[-1]["timestamp"]  # pre-sorted by sources.py
    if "received" in stages_seen:
        return "replied", last_at
    return "emailed", last_at


def reconcile_all(crm: dict, inbox: dict, scrape: dict, cost_tracker) -> list[dict]:
    """Builds canonical lead records from all three sources. Returns a list
    of dicts; leads whose conflict requires an LLM read are flagged with
    needs_llm_escalation=True but NOT yet resolved — planner.py decides,
    budget permitting, which of those actually get the (paid) LLM call."""

    all_ids = set(crm) | set(inbox) | set(scrape)
    results = []

    for lead_id in sorted(all_ids):
        crm_row = crm.get(lead_id)
        inbox_events = inbox.get(lead_id, [])
        scrape_row = scrape.get(lead_id)

        if crm_row is None:
            # Only seen via scrape (or inbox with no CRM record) — a genuinely
            # new prospect the sales team hasn't entered yet.
            results.append({
                "lead_id": lead_id,
                "name": None,
                "company": scrape_row["company"] if scrape_row else None,
                "email": None,
                "deal_value_gbp": 0.0,
                "crm_stage": None,
                "email_evidence_stage": _email_evidence_stage(inbox_events)[0],
                "last_email_event_at": _email_evidence_stage(inbox_events)[1],
                "canonical_stage": "prospect_not_in_crm",
                "conflict_type": None,
                "needs_llm_escalation": False,
                "industry": scrape_row.get("industry") if scrape_row else None,
                "resolution_note": "Found only in scrape/inbox, not yet in CRM — flagged for sales team to add.",
            })
            continue

        email_stage, last_email_at = _email_evidence_stage(inbox_events)
        crm_stage = crm_row["stage"]

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
        }

        if crm_stage in REQUIRES_REPLY_EVIDENCE and email_stage not in REQUIRES_REPLY_EVIDENCE:
            # The exact scenario from the brief: CRM says replied/qualified,
            # but there's no reply evidence in the inbox at all.
            lead["conflict_type"] = "crm_ahead_of_evidence"
            lead["needs_llm_escalation"] = True  # planner.py applies the value threshold
            lead["canonical_stage"] = None  # not yet resolved
            lead["resolution_note"] = None
        elif STAGE_ORDER[crm_stage] < STAGE_ORDER[email_stage]:
            # CRM hasn't caught up to reality — inbox is ground truth here,
            # and outranks CRM on quality (1.0 vs 0.75), so resolve immediately.
            lead["conflict_type"] = "crm_stale"
            lead["needs_llm_escalation"] = False
            lead["canonical_stage"] = email_stage
            lead["resolution_note"] = (
                f"CRM says '{crm_stage}' but inbox shows '{email_stage}' evidence "
                f"(quality 1.0 vs 0.75) — trusting inbox, CRM is behind."
            )
        else:
            lead["conflict_type"] = None
            lead["needs_llm_escalation"] = False
            lead["canonical_stage"] = crm_stage
            lead["resolution_note"] = "CRM stage matches or exceeds available email evidence — no conflict."

        results.append(lead)
        cost_tracker.record_rows_touched(1)

    return results
