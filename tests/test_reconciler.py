"""
Sanity tests for the reconciliation logic. Run with: pytest tests/
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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


if __name__ == "__main__":
    test_crm_ahead_of_evidence_is_flagged()
    test_crm_stale_resolves_deterministically_to_inbox()
    test_no_conflict_when_stages_agree()
    test_scrape_only_lead_becomes_prospect()
    print("All sanity tests passed.")
