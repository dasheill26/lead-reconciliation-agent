"""
Tests for CostTracker's accounting — the part of this project where a
silent arithmetic bug would be most embarrassing, since the whole point
of the brief is an honest cost report.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.cost_tracker import CostTracker, HAIKU_INPUT_PRICE_PER_MTOK_USD, HAIKU_OUTPUT_PRICE_PER_MTOK_USD, USD_TO_GBP


def test_api_calls_counted_by_type():
    t = CostTracker()
    t.record_api_call("crm_fetch")
    t.record_api_call("crm_fetch")
    t.record_api_call("inbox_fetch")
    r = t.report(leads_reconciled=1)
    assert r["api_calls_by_type"] == {"crm_fetch": 2, "inbox_fetch": 1}
    assert r["total_api_calls"] == 3


def test_llm_cost_matches_manual_calculation():
    t = CostTracker()
    t.record_llm_call(input_tokens=1_000_000, output_tokens=1_000_000, simulated=False)
    r = t.report(leads_reconciled=10)
    expected_usd = HAIKU_INPUT_PRICE_PER_MTOK_USD + HAIKU_OUTPUT_PRICE_PER_MTOK_USD
    expected_gbp = round(expected_usd * USD_TO_GBP, 4)
    assert r["model_inference_cost_gbp"] == expected_gbp


def test_cost_per_lead_divides_correctly():
    t = CostTracker()
    t.record_llm_call(input_tokens=500_000, output_tokens=0, simulated=False)
    r = t.report(leads_reconciled=5)
    assert r["cost_per_lead_gbp"] == round(r["model_inference_cost_gbp"] / 5, 5)


def test_zero_leads_reconciled_does_not_divide_by_zero():
    t = CostTracker()
    r = t.report(leads_reconciled=0)
    assert r["cost_per_lead_gbp"] == 0.0


def test_simulated_vs_real_tracked_separately():
    t = CostTracker()
    t.record_llm_call(100, 3, simulated=True)
    t.record_llm_call(100, 3, simulated=False)
    r = t.report(leads_reconciled=2)
    assert r["llm_calls_simulated"] == 1
    assert r["llm_calls_real"] == 1


if __name__ == "__main__":
    test_api_calls_counted_by_type()
    test_llm_cost_matches_manual_calculation()
    test_cost_per_lead_divides_correctly()
    test_zero_leads_reconciled_does_not_divide_by_zero()
    test_simulated_vs_real_tracked_separately()
    print("All cost tracker tests passed.")
