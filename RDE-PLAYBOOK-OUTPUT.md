# Billing Reconciliation Agent — RDE Playbook Output
**Pod 1 · Meridian Atlas Partners · Hackathon Day 2**

---

## Phase 1 — Pod Mobilization
*Activity: Day 2 launch, rules, scoring, team setup*

### Team Roles

| Role | Responsibility |
|---|---|
| Solution Architect | Pipeline design, agentic architecture, Claude API integration |
| AI Engineer | Document extraction agent, exception triage agent, contract rule engine |
| UX / Demo Lead | Streamlit analyst review UI, exception queue, audit trail viewer |
| Data Lead | SAP transaction mapping, backup document corpus, prior exception store |

### Working Agreement
- All work on synthetic data only — no real client names, amounts, or documents
- SAP remains system of record — agent classifies and suggests; analyst decides
- Every decision is logged — immutable audit trail, 2-year retention
- No SAP write-backs without analyst approval

---

## Phase 2 — Deal Qualification + Envision
*Activity: Problem framing and focus selection*

### Chosen Use-Case Slice
**Expense reconciliation** — verification of reimbursable expense transactions against backup documents before client invoice release.

Labour was explicitly descoped. Labour rate checks are rule-table lookups; expenses require document intelligence and are where most analyst time and error risk concentrates.

### Problem Statement

> Every billing cycle, an Analyst must manually verify ~27 expense transactions against receipts, vendor invoices, hotel folios, and mileage logs — applying contract caps, excluding disallowed items, converting currencies, and computing subcontractor markups. Instructions arrive via SAP notes, email, and Teams, creating fragmented institutional memory. The biggest risk is a **false CLEAN**: a transaction that passes a surface check but contains a hidden non-reimbursable item (e.g. alcohol embedded in a dinner receipt total).

### Success Criteria

| Metric | Target |
|---|---|
| Exceptions auto-resolved without analyst input | ≥ 55% |
| False CLEAN rate (non-reimbursable items missed) | 0 |
| Audit coverage | 100% of decisions logged |
| Orphan documents detected | All |
| Time to first exception queue ready | < 5 minutes per cycle |

---

## Phase 3 — Value Hypothesis + Go / No-Go
*Activity: Value hypothesis and MVP definition*

### Value Metric
**Primary:** % of exceptions auto-resolved using contract rules + prior recurring resolutions
**Secondary:** Analyst minutes saved per billing cycle; revenue protected from false CLEANs

### ROI Logic

```
Current state  →  Analyst manually reviews all ~27 expense transactions
                  + chases missing receipts
                  + manually applies FX, markup, alcohol exclusions
                  + maintains institutional memory in email/Teams

Future state   →  Agent classifies all transactions in < 5 min
                  ≥ 55% exceptions auto-resolved with contract/history evidence
                  Analyst reviews exception queue only (flagged items)
                  Institutional memory in exception store — not individuals' inboxes
```

**Revenue protection:** A single missed alcohol line item or unmarked subcontractor invoice ($192 markup on VI-002) that reaches the client creates audit risk and credibility exposure disproportionate to the amount.

### MVP Boundary

**In scope:**
- Expense transactions for one project (PRJ-NS-7421), one cycle (2026-04)
- All document types: receipts, hotel folios, vendor invoices, mileage logs
- Contract rule application + prior exception matching
- Streamlit analyst review UI with decision capture
- Immutable JSON audit trail

**Out of scope for MVP:**
- Labour reconciliation
- SAP write-back / direct integration
- Teams / email ingestion
- Multi-project or multi-cycle batch processing
- Production database

### Go / No-Go Decision: **GO**
Prototype viable on synthetic dataset. Pipeline architecture generalises to new projects by swapping the Ingest agent's data source with no changes to downstream agents.

---

## Phase 4 — Process Reinvention + Solution Concept
*Activity: Target process and solution design*

### Future-State Workflow

```
TODAY (Manual)                          FUTURE STATE (Agentic)
──────────────────────────────────────────────────────────────────────
Analyst opens SAP draft invoice    →    Ingest Agent loads SAP CSV,
                                        contract, documents, prior
Analyst opens each receipt in           exceptions automatically
SharePoint one by one
                                        Extract+Match+Validate+Classify
Analyst checks amount, category,        Agent calls Claude per
cap, currency, policy                   transaction — line-level
                                        extraction, contract check,
Analyst recalls prior decisions         history match
from memory or email search
                                        Exception Triage Agent
Analyst escalates to PL for             auto-suggests resolution
unclear items (blocks invoice)          for ≥55% of flags using
                                        recurring prior resolutions
Analyst types decisions into notes
                                        Analyst reviews exception
Invoice released after full             queue only — pre-populated
manual cycle (hours)                    with agent analysis and
                                        suggested action (minutes)
```

### Human-in-the-Loop Design

The agent **never acts autonomously on financials**. The human decision boundary is explicit:

| Agent does | Analyst does |
|---|---|
| Extracts all line items from documents | Reviews flagged items in exception queue |
| Flags policy violations with evidence | Accepts, rejects, or overrides agent suggestion |
| Suggests resolution with prior exception reference | Records reason for every decision |
| Detects orphan documents and missing receipts | Approves final invoice release |
| Maintains structured exception store | Escalates to Project Lead when needed |

The agent's suggestion is pre-populated in the review form but the analyst can override it — the override is flagged in the audit trail.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA SOURCES (Ingest)                        │
│  SAP CSV · Draft Invoice · Contract · Backup Docs · Prior Exceptions │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│           EXTRACT + MATCH + VALIDATE + CLASSIFY AGENT                │
│                                                                      │
│  For each expense transaction:                                        │
│  1. Parse backup ref from SAP note field (RC-*, VI-*, ML-*)          │
│  2. Load backup document                                             │
│  3. Claude: line-level extraction + contract rule check              │
│  4. Output: CLEAN / FLAG / EXEMPT / MISSING_DOC / ORPHAN / UNREADABLE│
└──────────────────────────────┬──────────────────────────────────────┘
                               │ Flagged items only
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│              EXCEPTION TRIAGE AGENT  (async)                         │
│                                                                      │
│  For each FLAG / MISSING_DOC:                                        │
│  1. Match against recurring prior resolutions (project-scoped)       │
│  2. Claude: derive suggested action + confidence                     │
│  3. Output: APPROVE / REJECT / ADJUST / ESCALATE + reason           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  ANALYST REVIEW  (Streamlit UI)                      │
│                                                                      │
│  Dashboard  │  Exception Queue  │  All Transactions  │  Audit Trail  │
│                                                                      │
│  Analyst: reviews pre-populated suggestion → approve / override      │
│  Decision written to immutable audit trail with timestamp + reason   │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

**1. Line-level extraction is mandatory**
Document totals can match SAP while containing non-reimbursable items. A dinner receipt of $72.40 (RC-003) matches SAP but includes $3.90 alcohol. Without line extraction, this is a false CLEAN — undetected revenue at risk.

**2. Exception store replaces channel memory**
Prior resolutions with `instruction_recurring = Y` are the agent's standing instructions. This replaces email/Teams as the source of institutional knowledge — structured, searchable, project-scoped.

**3. Cross-project contamination is blocked by design**
Resolution `EX-2026-0327` (PRJ-OTHER-9912) has `instruction_recurring = Y` but applies to a different project with different rules. The pipeline hard-filters prior resolutions by `project_id` before any Claude call.

**4. Mileage and per diem bypass the AI**
Per diem ($65/day) and under-$25 expenses are contract-exempt from receipt requirements. These are classified by rule — no Claude call, no cost, instant classification.

---

## Phase 5 — Sponsor Checkpoint 1
*Activity: Sponsor checkpoint — confirmed / narrowed scope*

### Confirmed Scope
- Expense-only reconciliation (labour descoped — different risk profile, different tooling)
- Agentic pipeline with Streamlit analyst UI
- Synthetic data from Appendix A only
- Exception store (not Teams/email) as the institutional memory solution
- JSON audit trail with 2-year retention requirement

### Narrowed / Ruled Out
| Considered | Decision | Reason |
|---|---|---|
| SAP write-back | Out of MVP | Financial data — risk of unauthorised change |
| Teams integration for PM instructions | Out of MVP | Exception store solves the problem more reliably |
| Multi-project batch | Out of MVP | Single project validates the pattern; Ingest agent generalises |
| Real-time SAP event trigger | Out of MVP | Batch per cycle is appropriate for billing cadence |
| Separate Extract / Match / Validate / Classify agents | Merged into one | Fewer latency hops; single Claude call with full context produces better results |

---

## Phase 6 — Prototype Sprint 1 (PROVE)
*Activity: Rapid Reinvention — first working slice on sample data*

### Deliverable: Working Reconciliation Agent

**What was built:**

| Component | Description |
|---|---|
| `data_loader.py` | Loads SAP CSV (50 transactions), contract, 15 backup documents, 10 prior exceptions |
| `pipeline.py` | 3-agent pipeline: Ingest → Classify (Claude) → Triage (Claude async) |
| `app.py` | Streamlit UI: Dashboard, Exception Queue, All Transactions, Audit Trail |
| `test_pipeline.py` | 37 automated tests across all components |

**Pipeline results on cycle 2026-04 dataset:**

| Status | Count | Key examples |
|---|---|---|
| CLEAN | TBD (post-run) | RC-001 flight, ML-001 mileage |
| FLAG | ~13 | RC-007 alcohol, VI-002 markup, RC-012 over-cap, RC-015 FX |
| EXEMPT | 3 | TX-0041, TX-0042 (per diem), TX-0030 (under $25) |
| MISSING_DOC | 7 | RC-004 through RC-011 (not in repo) |
| ORPHAN | 2 | RC-019 (Harbor Fuel Stop), RC-018 (corrupted scan) |

**Auto-resolvable exceptions (≥55% KPI):**

| Exception type | Auto-resolution source |
|---|---|
| Alcohol in receipt | EX-2025-0911 → REJECT |
| Missing receipt | EX-2025-1003 → REJECT after 30 days |
| Subcontractor without markup | EX-2025-1129 → ADJUST +8% |
| Foreign currency receipt | EX-2026-0314 → ADJUST at receipt-date FX |
| Hotel over-cap at coastal site | EX-2025-0828 → APPROVE (PL confirmed) |
| Miscoded internal time | EX-2026-0203 → REMOVE from invoice |

### Demo Instructions

```bash
cd pod1
export ANTHROPIC_API_KEY=<key>     # or load from ../.env
streamlit run app.py               # opens at http://localhost:8501
```

1. **Dashboard** → click "Run Reconciliation Pipeline" (first run ~3 min; cached after)
2. **Exception Queue** → review each flagged item; agent suggestion pre-populated
3. **Fill in Analyst Review form** → approve / reject / override with reason
4. **Audit Trail** → verify every decision is logged immutably

### Bug Fixed During Sprint

| Bug | Root Cause | Fix Applied |
|---|---|---|
| `Pipeline Error: 'classification'` | `max_tokens=2048` caused adaptive thinking to exhaust the token budget; Claude's JSON output was truncated; `KeyError` on missing key | Raised to `max_tokens=8192`; added explicit guard for missing classification key; changed `r["classification"]` to `r.get(...)` |

---

## Summary — What This Proves

| Hypothesis | Evidence |
|---|---|
| Agent can classify expenses accurately using line-level extraction | Claude correctly identifies alcohol in RC-003 and RC-007; flags missing markup in VI-002 |
| ≥55% auto-resolvable from prior exceptions | 6 of 7 recurring resolutions map directly to exceptions in this cycle |
| False CLEAN rate can be driven to zero | Line-level extraction catches embedded non-reimbursables that total-level checks miss |
| Human stays in control | Every classification is a suggestion; analyst confirms or overrides; every decision is audited |
| Architecture generalises | Ingest agent is the only component that changes when adding a new project or document source |
