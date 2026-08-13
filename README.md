# Lead Reconciliation Agent

An agent that maintains a single source of truth for sales leads across three
sources — a CRM export, an email inbox, and a scraped prospect list — that
update at different rates and sometimes disagree. It decides which source to
trust per-field, detects when a lead has moved through the pipeline, and
reports exactly what it cost in API calls and model inference to do it.

Built for a take-home engineering assessment. AI-assisted (Claude), and every
design decision below is one I can walk through and defend — that was the
actual point of the exercise, not just getting it to run.

## The core idea

Most of the reconciliation is **not** a job for an LLM. Comparing two
timestamps and checking whether a stage claim is supported by evidence is a
five-line rule, and running that rule is free. The interesting engineering
problem isn't "call an LLM for lead data" — it's **deciding when not to**,
and being able to prove, after the fact, that you didn't waste money doing so.

So the agent is built in two tiers:

1. **Deterministic rules** (`agent/reconciler.py`) handle the vast majority of
   leads — no conflict, or a conflict a fixed trust-weighting resolves
   confidently. Free, instant, no API call.
2. **LLM escalation** (`agent/llm_decider.py`), reserved for the one conflict
   type that's genuinely ambiguous, and only above a value threshold where
   getting it right is worth the (tiny) cost — see "Design decisions" below.

## Source trust model

| Source | Quality weight | Why |
|---|---|---|
| Inbox | 1.0 | Ground truth for whether an email was actually sent/replied to. Can't be forgotten or faked. |
| CRM | 0.75 | Authoritative for deal value and stage *intent*, but reps forget to update it — known to lag reality. |
| Scrape | 0.5 | Good for enrichment (industry, company size) and discovering brand-new prospects, but has no visibility into pipeline stage and is only as fresh as the last scrape. |

These are fixed, documented business judgement calls, not learned weights —
deliberately, so they're auditable and arguable rather than a black box.

## Design decisions (the parts worth defending)

**1. Value-gated LLM escalation.** When the CRM claims a stage (`replied` or
`qualified`) that the inbox has no evidence for, that's a real conflict. But
if the deal is worth £500, spending an API call to adjudicate it isn't worth
it — the deterministic fallback (trust the evidence, downgrade the stage) is
good enough. Only conflicts on deals ≥ **£10,000** (`LLM_ESCALATION_THRESHOLD_GBP`
in `planner.py`) are even considered for an LLM read. This is the concrete
answer to "detect when a naive approach would be wasteful."

**2. Budget-aware prioritization.** Even among high-value conflicts, the
agent estimates the total cost of escalating all of them before doing so. If
that exceeds the run's budget (`--budget`, default £0.01), it sorts by deal
value and only escalates as many as fit, deferring the rest to the
deterministic rule rather than silently dropping them or blowing the budget.
Every deferral is logged with the reason.

**3. Change-detection before re-processing.** Each source file is hashed on
every run and compared against the previous run's hash (`state/checksums.json`).
Unchanged sources are skipped entirely — no re-fetch, no re-parse, and (in a
real deployment) no billable API call. Run it twice in a row with no data
changes and you'll see API calls drop from the source fetches to zero.

**4. Dual-mode cost accounting.** If `ANTHROPIC_API_KEY` is set, the agent
makes real calls to `claude-haiku-4-5-20251001` and computes cost from the
real `usage.input_tokens` / `output_tokens` the API returns, priced at
Anthropic's published rate ($1/$5 per MTok). If no key is set, it falls back
to a deterministic simulated decision with an estimated token count — and
the report says so explicitly, in the report itself, not just in a log
line easy to miss. A cost report that can't tell you whether its numbers are
real is worse than useless.

**5. Haiku, not Sonnet or Opus.** The escalation task is a one-word
classification given a short, structured prompt — exactly the kind of task
where the frontier model buys you nothing. Using the cheapest current model
for a cheap task is itself part of the cost story.

## Example output

```
$ python run.py --once

Reconciled 16 leads (4 had a conflict to resolve).

ID    Name            CRM Stage   Canonical Stage     Note
----------------------------------------------------------------------------------------------------
L001  Priya Shah      qualified   emailed             Resolved via LLM read of the conflicting evidence...
L009  Hannah Ross     qualified   emailed             Resolved via LLM read of the conflicting evidence...
L015  Owen Clarke     qualified   emailed             CRM claims 'qualified' with no reply evidence, but deal...
L016  Freya Nash      qualified   qualified           Resolved via LLM read of the conflicting evidence...
...

============================================================
COST & DECISION REPORT
============================================================
API calls made:        6  {'crm_fetch': 1, 'inbox_fetch': 1, 'scrape_fetch': 1, 'anthropic_messages': 3}
Rows touched:          59
Leads reconciled:      16
LLM calls (real):      0
LLM calls (simulated): 3
  -> SIMULATED MODE: no ANTHROPIC_API_KEY set, cost below is
     an estimate using real Haiku 4.5 pricing on approximated
     token counts, not a real bill.
Model inference cost:  £0.0004
Cost per lead:         £0.00003

Decision log:
  - Source 'crm' changed since last run — processing.
  - L015 (Owen Clarke): conflict below value threshold (£2800 < £10,000) — resolved by rule, not escalated.
  - BUDGET CHECK: 3 leads need LLM escalation, estimated £0.0018 fits within £0.0100 budget — escalating all.
  - LLM (SIMULATED): L001 (Priya Shah) -> 'emailed' [~172 in / 3 out tokens, estimated]
============================================================
```

Run it again immediately with no data changes and the source `api_calls`
drop to zero — only the LLM calls remain, because nothing changed:

```
API calls made:        3  {'anthropic_messages': 3}
Decision log:
  - Source 'crm' unchanged since last run (hash match) — skipping fetch/parse.
  - Source 'inbox' unchanged since last run (hash match) — skipping fetch/parse.
  - Source 'scrape' unchanged since last run (hash match) — skipping fetch/parse.
```

Force a tight budget and watch it prioritize the highest-value conflict and
defer the rest:

```
$ python run.py --once --budget 0.001

  - BUDGET CHECK: 3 leads need LLM escalation, estimated £0.0018 exceeds
    £0.0010 budget — escalating only the top 1 by deal value, deferring 2
    to the deterministic rule.
  - LLM (SIMULATED): L016 (Freya Nash) -> 'qualified' [...]
```

(Freya Nash's deal is £31,500 — the largest of the three — so she's the one
that gets the real read; Priya Shah's £18,000 and Hannah Ross's £12,200 fall
back to the deterministic rule this run.)

## Running it

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: enable real LLM calls
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY

python run.py --once                    # single run, full report, exits
python run.py --once --budget 0.0005    # force the budget-cap path
python run.py --interval 15             # scheduled: runs every 15 minutes, forever
```

Without an API key, everything above still works exactly the same way —
the decision logic, the thresholds, the budget prioritization — just with
simulated (clearly labeled) LLM calls instead of real ones.

## Project structure

```
lead-reconciliation-agent/
├── agent/
│   ├── sources.py         # loaders + change-detection (hash-based skip logic)
│   ├── reconciler.py      # trust rules, conflict detection, stage inference
│   ├── llm_decider.py     # dual-mode (real/simulated) ambiguous-case resolver
│   ├── cost_tracker.py    # single source of truth for the cost report
│   └── planner.py         # orchestrates a run; the two "notice and adapt" decisions
├── data/
│   ├── crm.csv            # mock CRM export
│   ├── inbox.json         # mock email events
│   └── scrape.json        # mock scraped prospect list
├── state/                 # runtime state (checksums, last report) — gitignored
├── tests/test_reconciler.py
└── run.py                 # CLI entrypoint
```

## What's mocked vs real

The three data sources are static files, not live connections to a real CRM,
inbox, or scraper — building and authenticating against three real external
systems wasn't the point of the exercise, and would have burned the whole
time budget on plumbing instead of the actual reconciliation/cost-tracking
logic being assessed. The **LLM call is genuinely real** when a key is
provided, and the change-detection, trust-weighting, and budget logic all
operate exactly as they would against live sources — the file reads stand in
for what would be `crm_api.get_updated_since(...)`, `imap.fetch(...)`, and
`scraper.run(...)` calls.

## Known limitations / what I'd do with more time

- **Identity resolution is naive.** Leads are matched across sources by a
  shared `lead_id`, which real systems won't hand you for free — a CRM row,
  an email thread, and a scraped listing don't arrive pre-linked. With more
  time I'd add fuzzy matching on email domain + company name, with a
  confidence score, and a review queue for ambiguous merges.
- **State is a JSON checksum file, not a database.** Fine for a single-process
  demo; a real deployment needs a proper store (Postgres/SQLite) so
  concurrent runs and historical audit trails work.
- **Fixed trust weights, not learned ones.** Reasonable as a starting point
  and easy to defend in a review, but a mature version would track each
  source's actual historical accuracy per field and adjust weights over
  time.
- **USD→GBP is a fixed constant**, not a live rate — fine for this exercise,
  wrong for production.
- **The simulated-mode token estimate** (`len(prompt) // 4`) is a standard
  rough heuristic, not a real tokenizer count — clearly labeled as such
  everywhere it appears, but worth swapping for `anthropic.count_tokens` if
  this ever needs to be precise without a live key.
- **Source fetches run sequentially.** Three sources, fine. Thirty sources,
  I'd want them concurrent.
- **No retry/backoff on the real LLM path** — a production version needs to
  handle rate limits and transient failures gracefully rather than just
  falling back to simulated mode on any exception.

## Tests

```bash
pytest tests/
# or, no pytest installed:
python tests/test_reconciler.py
```
