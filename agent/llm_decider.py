"""
llm_decider.py

Handles the genuinely ambiguous reconciliation cases — the ones the
deterministic rules in reconciler.py can't confidently resolve. This is
deliberately the *only* place in the agent that calls an LLM: escalating
every lead to a model call would be the "naive/wasteful" approach the
brief explicitly warns against, so most leads never reach this file at
all (see planner.py for the threshold logic).

Dual mode:
  - If ANTHROPIC_API_KEY is set, calls the real Messages API with
    claude-haiku-4-5-20251001 (cheapest current model, appropriate for a
    single-sentence classification task — no reason to pay Sonnet/Opus
    prices for this) and reports real token usage from the response.
  - If not set, falls back to a deterministic simulated decision (hashed
    from the conflict context, so re-runs are reproducible) and an
    estimated token count, clearly flagged as simulated everywhere it
    surfaces — including in the cost report.
"""

import os
import hashlib
import re

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "You are a sales-ops assistant resolving a conflict between two sources "
    "of truth about a sales lead's pipeline stage. Given the CRM's claimed "
    "stage and the actual email evidence, respond with exactly one word: "
    "either the stage you believe is correct (new, emailed, replied, or "
    "qualified), or UNCLEAR if the evidence genuinely doesn't support a "
    "confident call. Be conservative — prefer the stage the evidence "
    "actually supports over what the CRM claims."
)


def _build_prompt(lead: dict) -> str:
    return (
        f"Lead: {lead['name']} at {lead['company']}\n"
        f"CRM stage: {lead['crm_stage']} (CRM last updated: {lead['crm_last_updated']})\n"
        f"Email evidence stage: {lead['email_evidence_stage']} "
        f"(most recent email event: {lead.get('last_email_event_at', 'none')})\n"
        f"Deal value: £{lead['deal_value_gbp']}\n"
        f"What is the correct current pipeline stage?"
    )


def _simulated_decision(lead: dict) -> tuple[str, int, int]:
    """Deterministic stand-in for a real model call: hashes the conflict
    context to pick a reproducible answer, and estimates token counts from
    prompt length (roughly 4 chars/token, a standard rough heuristic)."""
    prompt = _build_prompt(lead)
    h = hashlib.sha256(prompt.encode()).hexdigest()
    # Lean toward the (more conservative) email-evidence stage most of the
    # time, occasionally siding with CRM — mimics a real model's tendency
    # to follow the instruction to "be conservative" without being 100%
    # deterministic in direction.
    options = [lead["email_evidence_stage"]] * 3 + [lead["crm_stage"], "UNCLEAR"]
    decision = options[int(h, 16) % len(options)]
    input_tokens = max(1, len(SYSTEM_PROMPT + prompt) // 4)
    output_tokens = 3  # one word out
    return decision, input_tokens, output_tokens


def _real_decision(lead: dict) -> tuple[str, int, int]:
    import anthropic  # imported lazily so simulated mode never needs the package installed

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    prompt = _build_prompt(lead)
    response = client.messages.create(
        model=MODEL,
        max_tokens=10,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    match = re.search(r"(new|emailed|replied|qualified|unclear)", text, re.IGNORECASE)
    decision = match.group(1).lower() if match else "UNCLEAR"
    if decision == "unclear":
        decision = "UNCLEAR"
    return decision, response.usage.input_tokens, response.usage.output_tokens


def decide(lead: dict, cost_tracker) -> str:
    """Returns the resolved stage string ('new'/'emailed'/'replied'/
    'qualified'/'UNCLEAR') and records the call — real or simulated — on
    the shared cost tracker."""
    use_real = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_real:
        try:
            decision, in_tok, out_tok = _real_decision(lead)
            cost_tracker.record_llm_call(in_tok, out_tok, simulated=False)
            cost_tracker.log_decision(
                f"LLM (real): {lead['lead_id']} ({lead['name']}) -> '{decision}' "
                f"[{in_tok} in / {out_tok} out tokens]"
            )
            return decision
        except Exception as e:
            cost_tracker.log_decision(
                f"LLM call failed for {lead['lead_id']} ({e}); falling back to simulated mode"
            )
            use_real = False

    decision, in_tok, out_tok = _simulated_decision(lead)
    cost_tracker.record_llm_call(in_tok, out_tok, simulated=True)
    cost_tracker.log_decision(
        f"LLM (SIMULATED): {lead['lead_id']} ({lead['name']}) -> '{decision}' "
        f"[~{in_tok} in / {out_tok} out tokens, estimated]"
    )
    return decision
