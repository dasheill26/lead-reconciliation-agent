"""
Sanity tests for the reconciliation logic. Run with: pytest tests/
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timezone

from agent.cost_tracker import CostTracker
from agent import reconciler


def _tracker():
    return CostTracker()


def test_crm_ahead_of_evidence_is_flagged():
    crm = {"L1": {"lead_id": "L1", "name": "Test", "company": "Co", "email": "a@b.com",
                  "stage": "qualified", "deal_value_gbp": 5000.0, "last_updated": "2026-08-01T00:00:00Z"}}
    inbox = {"L1": [{"lead_id": "L1", "direction": "sent", "timestamp": "2026-08-01T09:00:00Z"}]}
    scrape = {}
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker())
    lead = leads[0]
    assert lead["conflict_type"] == "crm_ahead_of_evidence"
    assert lead["needs_llm_escalation"] is True
    assert lead["canonical_stage"] is None  # not yet resolved — planner's job


def test_crm_stale_resolves_deterministically_to_inbox():
    crm = {"L2": {"lead_id": "L2", "name": "Test2", "company": "Co2", "email": "c@d.com",
                  "stage": "emailed", "deal_value_gbp": 1000.0, "last_updated": "2026-08-01T00:00:00Z"}}
    inbox = {"L2": [
        {"lead_id": "L2", "direction": "sent", "timestamp": "2026-08-01T09:00:00Z"},
        {"lead_id": "L2", "direction": "received", "timestamp": "2026-08-02T09:00:00Z"},
    ]}
    scrape = {}
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker())
    lead = leads[0]
    assert lead["conflict_type"] == "crm_stale"
    assert lead["canonical_stage"] == "replied"
    assert lead["needs_llm_escalation"] is False


def test_no_conflict_when_stages_agree():
    crm = {"L3": {"lead_id": "L3", "name": "Test3", "company": "Co3", "email": "e@f.com",
                  "stage": "emailed", "deal_value_gbp": 1000.0, "last_updated": "2026-08-01T00:00:00Z"}}
    inbox = {"L3": [{"lead_id": "L3", "direction": "sent", "timestamp": "2026-08-01T09:00:00Z"}]}
    scrape = {}
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker())
    lead = leads[0]
    assert lead["conflict_type"] is None
    assert lead["canonical_stage"] == "emailed"


def test_scrape_only_lead_becomes_prospect():
    crm = {}
    inbox = {}
    scrape = {"L4": {"lead_id": "L4", "company": "New Co", "industry": "Retail",
                      "employee_count": 5, "scraped_at": "2026-08-01T00:00:00Z"}}
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker())
    lead = leads[0]
    assert lead["canonical_stage"] == "prospect_not_in_crm"


def test_stale_scrape_excluded_from_enrichment():
    """A scrape record older than the threshold should be dropped from the
    canonical record (industry/employee_count = None) and flagged, not
    silently trusted — this is the brief's 'deprioritise stale scrape data'
    example."""
    crm = {"L5": {"lead_id": "L5", "name": "Test5", "company": "Co5", "email": "g@h.com",
                  "stage": "new", "deal_value_gbp": 1000.0, "last_updated": "2026-08-01T00:00:00Z"}}
    inbox = {}
    scrape = {"L5": {"lead_id": "L5", "company": "Co5", "industry": "Retail",
                      "employee_count": 20, "scraped_at": "2026-07-01T00:00:00Z"}}  # very old
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker(), now=now)
    lead = leads[0]
    assert lead["scrape_stale"] is True
    assert lead["industry"] is None  # stale enrichment excluded


def test_fresh_scrape_is_used():
    crm = {"L6": {"lead_id": "L6", "name": "Test6", "company": "Co6", "email": "i@j.com",
                  "stage": "new", "deal_value_gbp": 1000.0, "last_updated": "2026-08-01T00:00:00Z"}}
    inbox = {}
    scrape = {"L6": {"lead_id": "L6", "company": "Co6", "industry": "Retail",
                      "employee_count": 20, "scraped_at": "2026-08-12T00:00:00Z"}}  # 1 day old
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker(), now=now)
    lead = leads[0]
    assert lead["scrape_stale"] is False
    assert lead["industry"] == "Retail"


def test_malformed_crm_stage_does_not_crash():
    """Real CRM exports have bad data sometimes — a typo'd or legacy stage
    value shouldn't take down the whole run."""
    crm = {"L7": {"lead_id": "L7", "name": "Test7", "company": "Co7", "email": "k@l.com",
                  "stage": "won", "deal_value_gbp": 1000.0, "last_updated": "2026-08-01T00:00:00Z"}}  # not a known stage
    inbox = {"L7": [{"lead_id": "L7", "direction": "sent", "timestamp": "2026-08-01T09:00:00Z"}]}
    scrape = {}
    leads = reconciler.reconcile_all(crm, inbox, scrape, _tracker())  # should not raise
    lead = leads[0]
    assert lead["conflict_type"] == "crm_invalid_stage"
    assert lead["canonical_stage"] == "emailed"  # falls back to email evidence


if __name__ == "__main__":
    test_crm_ahead_of_evidence_is_flagged()
    test_crm_stale_resolves_deterministically_to_inbox()
    test_no_conflict_when_stages_agree()
    test_scrape_only_lead_becomes_prospect()
    test_stale_scrape_excluded_from_enrichment()
    test_fresh_scrape_is_used()
    test_malformed_crm_stage_does_not_crash()
    print("All sanity tests passed.")
