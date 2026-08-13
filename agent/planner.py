"""
planner.py

Ties everything together and makes the two "notice a problem, change
course" decisions the brief asks for, beyond the staleness check that
lives in reconciler.py:

  1. Value-gated escalation: a crm_ahead_of_evidence conflict on a small
     deal isn't worth an LLM call — the deterministic downgrade-to-evidence
     rule is good enough. Only conflicts on deals >= LLM_ESCALATION_THRESHOLD_GBP
     are considered for the (paid) LLM read at all.

  2. Budget-aware prioritization: even among those, if the estimated cost
     of escalating all of them would exceed the run's budget, sort by deal
     value and only escalate as many as fit — the rest fall back to the
     deterministic rule rather than being silently dropped.

Both decisions are extracted into pure functions (apply_value_threshold,
apply_budget) that take/return plain data and don't touch the filesystem or
network — so they're testable in isolation, without mocking sources or the
LLM. See tests/test_planner.py.
"""

from . import sources, reconciler, llm_decider

LLM_ESCALATION_THRESHOLD_GBP = 10_000
ESTIMATED_COST_PER_LLM_CALL_GBP = 0.0006  # rough estimate for budgeting, refined post-hoc from real usage


def apply_value_threshold(leads: list[dict], threshold_gbp: float, cost_tracker) -> list[dict]:
    """Splits leads flagged needs_llm_escalation into those actually worth
    escalating (deal value >= threshold) vs those resolved immediately by
    the deterministic rule. Mutates the low-value leads in place (sets
    canonical_stage); returns the list of genuine candidates."""
    candidates = []
    for lead in leads:
        if not lead.get("needs_llm_escalation"):
            continue
        if lead["deal_value_gbp"] >= threshold_gbp:
            candidates.append(lead)
        else:
            lead["canonical_stage"] = lead["email_evidence_stage"]
            lead["resolution_note"] = (
                f"CRM claims '{lead['crm_stage']}' with no reply evidence, but deal value "
                f"(£{lead['deal_value_gbp']:.0f}) is below the £{threshold_gbp:,.0f} "
                f"LLM-escalation threshold — auto-resolved via rule (downgraded to evidence-supported "
                f"stage) rather than spending an API call on it."
            )
            cost_tracker.log_decision(
                f"{lead['lead_id']} ({lead['name']}): conflict below value threshold "
                f"(£{lead['deal_value_gbp']:.0f} < £{threshold_gbp:,.0f}) — resolved by rule, not escalated."
            )
    return candidates


def apply_budget(candidates: list[dict], budget_gbp: float, cost_tracker,
                  cost_per_call_gbp: float = ESTIMATED_COST_PER_LLM_CALL_GBP) -> list[dict]:
    """Given escalation candidates (already past the value threshold),
    decides how many can actually be escalated within budget, prioritizing
    highest deal value first. Mutates and resolves the deferred leads via
    the deterministic rule; returns the list still to be escalated."""
    if not candidates:
        return []

    estimated_total = len(candidates) * cost_per_call_gbp
    if estimated_total <= budget_gbp:
        cost_tracker.log_decision(
            f"BUDGET CHECK: {len(candidates)} leads need LLM escalation, "
            f"estimated £{estimated_total:.4f} fits within £{budget_gbp:.4f} budget — escalating all."
        )
        return candidates

    candidates_sorted = sorted(candidates, key=lambda l: l["deal_value_gbp"], reverse=True)
    affordable = max(1, int(budget_gbp / cost_per_call_gbp))
    to_escalate = candidates_sorted[:affordable]
    deferred = candidates_sorted[affordable:]

    cost_tracker.log_decision(
        f"BUDGET CHECK: {len(candidates)} leads need LLM escalation, "
        f"estimated £{estimated_total:.4f} exceeds £{budget_gbp:.4f} budget — "
        f"escalating only the top {len(to_escalate)} by deal value, "
        f"deferring {len(deferred)} to the deterministic rule."
    )
    for lead in deferred:
        lead["canonical_stage"] = lead["email_evidence_stage"]
        lead["resolution_note"] = (
            "High-value conflict, but deferred past budget cap — resolved via rule "
            "(downgraded to evidence-supported stage) instead of an LLM call."
        )
    return to_escalate


def run(cost_tracker, budget_gbp: float = 0.01) -> list[dict]:
    changed = sources.check_which_sources_changed(cost_tracker)

    crm = sources.load_crm(cost_tracker, changed["crm"])
    inbox = sources.load_inbox(cost_tracker, changed["inbox"])
    scrape = sources.load_scrape(cost_tracker, changed["scrape"])

    leads = reconciler.reconcile_all(crm, inbox, scrape, cost_tracker)

    candidates = apply_value_threshold(leads, LLM_ESCALATION_THRESHOLD_GBP, cost_tracker)
    to_escalate = apply_budget(candidates, budget_gbp, cost_tracker)

    for lead in to_escalate:
        decision = llm_decider.decide(lead, cost_tracker)
        if decision == "UNCLEAR":
            lead["canonical_stage"] = lead["email_evidence_stage"]
            lead["resolution_note"] = (
                "LLM found the conflict genuinely unclear — defaulted to the conservative, "
                "evidence-supported stage rather than trusting CRM's unconfirmed claim."
            )
        else:
            lead["canonical_stage"] = decision
            lead["resolution_note"] = f"Resolved via LLM read of the conflicting evidence: '{decision}'."

    return leads
