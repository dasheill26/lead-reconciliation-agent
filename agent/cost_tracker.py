"""
cost_tracker.py

Every operation the agent performs that has a real-world cost — a source
fetch, a row touched, an LLM call — passes through here. Nothing else in
the codebase computes cost independently; this is the single source of
truth so the final report can't drift from what actually happened.

Pricing (Claude Haiku 4.5, verified against Anthropic's published rates,
Aug 2026): $1.00 / MTok input, $5.00 / MTok output.
USD -> GBP uses a fixed approximate rate (documented below) rather than a
live FX API, since pulling a live rate for a cost report is its own can of
worms and out of scope here — flagged clearly so it's not mistaken for
more precision than it has.
"""

from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone

HAIKU_INPUT_PRICE_PER_MTOK_USD = 1.00
HAIKU_OUTPUT_PRICE_PER_MTOK_USD = 5.00
USD_TO_GBP = 0.74  # approximate mid-market rate, Aug 2026 — update as needed


@dataclass
class CostTracker:
    api_calls_by_type: dict = field(default_factory=lambda: defaultdict(int))
    rows_touched: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_calls_real: int = 0
    llm_calls_simulated: int = 0
    decisions: list = field(default_factory=list)  # human-readable decision log
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def record_api_call(self, call_type: str, count: int = 1):
        """Count a non-LLM API call (source fetch, etc). No £ cost attached —
        these are rate-limited, not billed per-call, in a typical CRM/IMAP/scraper
        setup. We still count them because the brief explicitly wants call counts,
        not just spend."""
        self.api_calls_by_type[call_type] += count

    def record_rows_touched(self, n: int):
        self.rows_touched += n

    def record_llm_call(self, input_tokens: int, output_tokens: int, simulated: bool):
        self.api_calls_by_type["anthropic_messages"] += 1
        self.llm_input_tokens += input_tokens
        self.llm_output_tokens += output_tokens
        if simulated:
            self.llm_calls_simulated += 1
        else:
            self.llm_calls_real += 1

    def log_decision(self, message: str):
        """Human-readable audit trail — this is what makes the report defensible
        rather than just a pile of numbers. Every non-trivial choice the agent
        makes gets one line here."""
        self.decisions.append(message)

    @property
    def llm_inference_cost_usd(self) -> float:
        cost = (self.llm_input_tokens / 1_000_000) * HAIKU_INPUT_PRICE_PER_MTOK_USD
        cost += (self.llm_output_tokens / 1_000_000) * HAIKU_OUTPUT_PRICE_PER_MTOK_USD
        return cost

    @property
    def llm_inference_cost_gbp(self) -> float:
        return self.llm_inference_cost_usd * USD_TO_GBP

    def report(self, leads_reconciled: int) -> dict:
        total_api_calls = sum(self.api_calls_by_type.values())
        cost_gbp = round(self.llm_inference_cost_gbp, 4)
        cost_per_lead = round(cost_gbp / leads_reconciled, 5) if leads_reconciled else 0.0
        return {
            "run_started_at": self.started_at,
            "total_api_calls": total_api_calls,
            "api_calls_by_type": dict(self.api_calls_by_type),
            "rows_touched": self.rows_touched,
            "leads_reconciled": leads_reconciled,
            "llm_calls_real": self.llm_calls_real,
            "llm_calls_simulated": self.llm_calls_simulated,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "model_inference_cost_gbp": cost_gbp,
            "cost_per_lead_gbp": cost_per_lead,
            "decisions": self.decisions,
        }

    def print_report(self, leads_reconciled: int):
        r = self.report(leads_reconciled)
        print("\n" + "=" * 60)
        print("COST & DECISION REPORT")
        print("=" * 60)
        print(f"API calls made:        {r['total_api_calls']}  {r['api_calls_by_type']}")
        print(f"Rows touched:          {r['rows_touched']}")
        print(f"Leads reconciled:      {r['leads_reconciled']}")
        print(f"LLM calls (real):      {r['llm_calls_real']}")
        print(f"LLM calls (simulated): {r['llm_calls_simulated']}")
        if r["llm_calls_simulated"] > 0:
            print("  -> SIMULATED MODE: no ANTHROPIC_API_KEY set, cost below is")
            print("     an estimate using real Haiku 4.5 pricing on approximated")
            print("     token counts, not a real bill.")
        print(f"Model inference cost:  £{r['model_inference_cost_gbp']}")
        print(f"Cost per lead:         £{r['cost_per_lead_gbp']}")
        print("\nDecision log:")
        for d in r["decisions"]:
            print(f"  - {d}")
        print("=" * 60 + "\n")
