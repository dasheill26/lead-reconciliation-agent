"""
Tests for the two extracted decision functions in planner.py. These are
pure functions over plain dicts — no filesystem, no network, no LLM — so
they're tested directly without mocking anything.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.cost_tracker import CostTracker
from agent.planner import apply_value_threshold, apply_budget


def _escalation_lead(lead_id, deal_value, crm_stage="qualified", email_stage="emailed"):
    return {
        "lead_id": lead_id, "name": f"Name-{lead_id}", "deal_value_gbp": deal_value,
        "crm_stage": crm_stage, "email_evidence_stage": email_stage,
        "needs_llm_escalation": True, "canonical_stage": None, "resolution_note": None,
    }


def test_low_value_conflict_resolved_without_escalation():
    tracker = CostTracker()
    leads = [_escalation_lead("L1", 2000)]
    candidates = apply_value_threshold(leads, threshold_gbp=10_000, cost_tracker=tracker)
    assert candidates == []
    assert leads[0]["canonical_stage"] == "emailed"  # downgraded to evidence stage
    assert any("below value threshold" in d for d in tracker.decisions)


def test_high_value_conflict_becomes_candidate():
    tracker = CostTracker()
    leads = [_escalation_lead("L2", 15_000)]
    candidates = apply_value_threshold(leads, threshold_gbp=10_000, cost_tracker=tracker)
    assert len(candidates) == 1
    assert candidates[0]["lead_id"] == "L2"
    assert candidates[0]["canonical_stage"] is None  # not resolved yet — planner's LLM step does that


def test_budget_allows_all_when_affordable():
    tracker = CostTracker()
    candidates = [_escalation_lead("L1", 15_000), _escalation_lead("L2", 20_000)]
    to_escalate = apply_budget(candidates, budget_gbp=1.0, cost_tracker=tracker, cost_per_call_gbp=0.001)
    assert len(to_escalate) == 2


def test_budget_defers_lowest_value_when_tight():
    tracker = CostTracker()
    candidates = [
        _escalation_lead("L1", 12_000),
        _escalation_lead("L2", 30_000),
        _escalation_lead("L3", 18_000),
    ]
    # cost_per_call=0.001, budget=0.0015 -> affords exactly 1 call
    to_escalate = apply_budget(candidates, budget_gbp=0.0015, cost_tracker=tracker, cost_per_call_gbp=0.001)
    assert len(to_escalate) == 1
    assert to_escalate[0]["lead_id"] == "L2"  # highest value (30k) wins the budget
    # the two deferred leads should already be resolved via the fallback rule
    deferred = [c for c in candidates if c["lead_id"] != "L2"]
    for lead in deferred:
        assert lead["canonical_stage"] == lead["email_evidence_stage"]
    assert any("deferring 2" in d for d in tracker.decisions)


def test_empty_candidates_returns_empty():
    tracker = CostTracker()
    assert apply_budget([], budget_gbp=1.0, cost_tracker=tracker) == []


if __name__ == "__main__":
    test_low_value_conflict_resolved_without_escalation()
    test_high_value_conflict_becomes_candidate()
    test_budget_allows_all_when_affordable()
    test_budget_defers_lowest_value_when_tight()
    test_empty_candidates_returns_empty()
    print("All planner tests passed.")
