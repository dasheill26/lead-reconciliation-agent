"""
planner.py

Ties everything together and makes the two "notice a problem, change
course" decisions the brief asks for:

  1. Value-gated escalation: a crm_ahead_of_evidence conflict on a small
     deal isn't worth an LLM call — the deterministic downgrade-to-evidence
     rule is good enough. Only conflicts on deals >= LLM_ESCALATION_THRESHOLD_GBP
     are considered for the (paid) LLM read at all.

  2. Budget-aware prioritization: even among those, if the estimated cost
     of escalating all of them would exceed the run's budget, sort by deal
     value and only escalate as many as fit — the rest fall back to the
     deterministic rule rather than being silently dropped.
"""

from . import sources, reconciler, llm_decider

LLM_ESCALATION_THRESHOLD_GBP = 10_000
ESTIMATED_COST_PER_LLM_CALL_GBP = 0.0006  # rough estimate for budgeting, refined post-hoc from real usage


def run(cost_tracker, budget_gbp: float = 0.01) -> list[dict]:
    changed = sources.check_which_sources_changed(cost_tracker)

    crm = sources.load_crm(cost_tracker, changed["crm"])
    inbox = sources.load_inbox(cost_tracker, changed["inbox"])
    scrape = sources.load_scrape(cost_tracker, changed["scrape"])

    leads = reconciler.reconcile_all(crm, inbox, scrape, cost_tracker)

    # --- Decision 1: value-gated escalation ---
    escalation_candidates = []
    for lead in leads:
        if not lead.get("needs_llm_escalation"):
            continue
        if lead["deal_value_gbp"] >= LLM_ESCALATION_THRESHOLD_GBP:
            escalation_candidates.append(lead)
        else:
            lead["canonical_stage"] = lead["email_evidence_stage"]
            lead["resolution_note"] = (
                f"CRM claims '{lead['crm_stage']}' with no reply evidence, but deal value "
                f"(£{lead['deal_value_gbp']:.0f}) is below the £{LLM_ESCALATION_THRESHOLD_GBP:,} "
                f"LLM-escalation threshold — auto-resolved via rule (downgraded to evidence-supported "
                f"stage) rather than spending an API call on it."
            )
            cost_tracker.log_decision(
                f"{lead['lead_id']} ({lead['name']}): conflict below value threshold "
                f"(£{lead['deal_value_gbp']:.0f} < £{LLM_ESCALATION_THRESHOLD_GBP:,}) — "
                f"resolved by rule, not escalated."
            )

    # --- Decision 2: budget-aware prioritization among the rest ---
    if escalation_candidates:
        estimated_total = len(escalation_candidates) * ESTIMATED_COST_PER_LLM_CALL_GBP
        if estimated_total > budget_gbp:
            escalation_candidates.sort(key=lambda l: l["deal_value_gbp"], reverse=True)
            affordable = max(1, int(budget_gbp / ESTIMATED_COST_PER_LLM_CALL_GBP))
            deferred = escalation_candidates[affordable:]
            escalation_candidates = escalation_candidates[:affordable]
            cost_tracker.log_decision(
                f"BUDGET CHECK: {len(escalation_candidates) + len(deferred)} leads need LLM escalation, "
                f"estimated £{estimated_total:.4f} exceeds £{budget_gbp:.4f} budget — "
                f"escalating only the top {len(escalation_candidates)} by deal value, "
                f"deferring {len(deferred)} to the deterministic rule."
            )
            for lead in deferred:
                lead["canonical_stage"] = lead["email_evidence_stage"]
                lead["resolution_note"] = (
                    "High-value conflict, but deferred past budget cap — resolved via rule "
                    "(downgraded to evidence-supported stage) instead of an LLM call."
                )
        else:
            cost_tracker.log_decision(
                f"BUDGET CHECK: {len(escalation_candidates)} leads need LLM escalation, "
                f"estimated £{estimated_total:.4f} fits within £{budget_gbp:.4f} budget — escalating all."
            )

        for lead in escalation_candidates:
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
