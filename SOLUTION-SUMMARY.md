# Reconciliation Agent — Solution Summary
**Project:** PRJ-NS-7421 · Northstar Civic Group · MSA-NS-2024-0418  
**Cycle:** 2026-04 · **Team:** Meridian Atlas Partners — Pod 1

---

## Problem Statement

SAP generates a draft invoice each month from unbilled transactions. Before the invoice can be released to the client, an Analyst must manually:
- Verify every expense against its backup document (receipt, vendor invoice, hotel folio, mileage log)
- Apply contract rules (caps, alcohol exclusions, subcontractor markup, FX conversion)
- Follow Project Lead instructions that may override or supplement the contract
- Flag exceptions, seek resolutions, and record decisions

This is time-consuming, error-prone, and entirely manual. The goal is to reduce billing reconciliation time using an agentic pipeline.

**Scope decision:** Expenses only (not labour). Labour reconciliation is a separate problem; expenses are where document verification is most complex and most error-prone.

---

## Solution Architecture — Four-Agent Pipeline

```
┌─────────┐    ┌────────────────────────────────────┐    ┌────────────────────┐    ┌─────────────────┐
│  Ingest  │───▶│  Extract + Match + Validate +       │───▶│  Exception Triage  │───▶│  Analyst Review │
│  Agent   │    │  Classify Agent (Claude opus-4-8)  │    │  Agent (async)     │    │  (Streamlit UI) │
└─────────┘    └────────────────────────────────────┘    └────────────────────┘    └─────────────────┘
```

### Key Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Separate Ingest agent | Yes | Allows new data sources (new SAP extracts, new document repos) to be added without touching downstream agents |
| Combine Extract+Match+Validate+Classify | Into one agent | Reduces latency; a single Claude call with full context produces better analysis than chained single-purpose calls |
| Exception Triage is async | Yes | Does not block the main classification loop; analyst review can proceed while triage runs |
| Analyst Review is async | Yes | Clean lines release without waiting for exception resolution; exception loop runs independently |
| No database | JSON files | Demo-appropriate; production would use a proper store with 2-year retention enforced |
| AI model | claude-opus-4-8 with adaptive thinking | Best-in-class reasoning for line-level document extraction and policy application |

---

## Match Key Design

The link between a SAP transaction and its backup document is the `note` field in the unbilled transactions CSV:

```
Receipt: RC-001
Vendor invoice: VI-002 (cost). Markup +8% to be added per contract
Mileage log: ML-001
```

The pipeline parses the first `RC-*`, `VI-*`, or `ML-*` reference from this field as the primary backup document reference. This drives the entire matching logic.

**Transactions with no parseable reference and amount > $25 are automatically flagged** — no receipt, no billing.

---

## Classification Scheme

| Status | Meaning | Example |
|---|---|---|
| `CLEAN` | Amount matches, no policy violation | RC-001 flight matches SAP |
| `FLAG` | Policy violation, amount mismatch, or ambiguity | Alcohol in dinner receipt; missing markup |
| `EXEMPT` | No receipt required by contract | Per diem ($65/day), under-$25 threshold |
| `MISSING_DOC` | Document referenced in SAP but not found in repo | RC-004, RC-005, RC-006, RC-008–RC-011 |
| `ORPHAN` | Document in repo with no matching SAP transaction | RC-019 (Harbor Fuel Stop receipt) |
| `UNREADABLE` | Document cannot be parsed, or Claude response malformed | RC-018 (corrupted scan) |

---

## Key Design Decisions (from design session)

### 1. Line-level extraction — the most critical decision

Documents must be analysed line by line, not as a total. A document-level check misses embedded non-reimbursable items.

**Example in dataset:**
- `RC-003` (hotel folio): dinner charge of $72.40 includes a $3.90 complimentary wine → alcohol not reimbursable regardless of amount
- `RC-007` (team dinner): total $126.85, SAP records $46.20 as "beverages", but receipt contains House red × 2 at $18 → alcohol must be excluded

**Failure mode this prevents:** False CLEAN — the biggest single failure mode. A transaction passes all checks but contains a non-reimbursable item embedded in the total.

### 2. Contract rules as first-class input

The contract (MSA-NS-2024-0418) is passed directly to Claude on every classification call. This enables the agent to:
- Detect missing subcontractor markup (VI-002: $2,400 cost → $2,592 billable at +8%)
- Apply meal caps ($90/day with receipt)
- Apply lodging caps ($275 major metro / $195 elsewhere)
- Reject alcohol under any circumstance
- Flag personal items (RC-017 laundry)
- Validate travel time billing (50% rate, capped 8 hrs per direction)

### 3. Exception store with `instruction_recurring` flag

Prior exception resolutions are the agent's institutional memory. Only resolutions where `instruction_recurring = Y` are applied as standing rules. Non-recurring resolutions are informational only.

**This replaces the alternative of Teams/email channel integration** — fragmented channel instructions cannot be reliably ingested; the exception store enforces a structured record.

**7 of 10 prior resolutions are recurring**, providing the ≥55% auto-resolvable KPI target.

### 4. Cross-project contamination guard

`EX-2026-0327` is a recurring resolution from `PRJ-OTHER-9912` (a different project, different rules). Even though `instruction_recurring = Y`, it must **never** be applied to PRJ-NS-7421. The pipeline filters prior resolutions by `project_id` before passing them to any Claude call.

### 5. Audit trail is immutable

Every pipeline run and every analyst decision is appended to `state/audit_trail.json`. Nothing is ever deleted or overwritten. Required retention: 2 years.

Each analyst decision captures:
- Transaction ID, action, adjusted amount (if any)
- Analyst name and timestamp
- Whether it was a manual override of the agent's suggestion
- Full reasoning

**Analysts must not need to re-open SAP** — the exception record is self-contained with all context needed for a decision.

### 6. Unreadable documents

If a document cannot be parsed (corrupted scan, low OCR confidence, illegible), classify as `UNREADABLE` and require analyst action. Do not attempt to infer amounts from noise.

### 7. SAP is the system of record

The solution integrates with SAP — it does not replace it. SAP amounts are the reference point; the agent identifies discrepancies and suggests adjustments. No SAP write-backs occur without analyst approval.

---

## Edge Cases in the Dataset

| Transaction | Issue | Agent behaviour |
|---|---|---|
| TX-0025 / RC-003 | Hotel folio: dinner $72.40 includes $3.90 wine | FLAG — alcohol line extracted, non-reimbursable |
| TX-0029 / RC-007 | Team dinner: SAP $46.20 vs receipt $126.85; alcohol included | FLAG — alcohol items listed; prior EX-2025-0911 auto-suggests REJECT |
| TX-0037 | Drafting supplies $67.30 — no receipt | FLAG + auto REJECT (EX-2025-1003: not billable after 30 days) |
| TX-0040 / RC-012 | Hotel $310 vs $195 elsewhere cap | FLAG — prior EX-2025-0828 auto-approves (PL confirmed no alternative) |
| TX-0043 / RC-013 | Client dinner $118 vs $90/day cap | FLAG — PL note on file; escalate for confirmation |
| TX-0044 / VI-002 | Subcontractor $2,400 — markup not applied | FLAG + auto ADJUST to $2,592 (EX-2025-1129) |
| TX-0048 / RC-015 | Receipt in CAD on USD project | FLAG + auto ADJUST with FX note (EX-2026-0314) |
| TX-0050 / RC-017 | Personal laundry $22 | FLAG — personal item, not reimbursable |
| RC-019 | Fuel receipt in repo, no SAP transaction | ORPHAN — escalate: missing transaction or irrelevant doc |
| RC-018 | Corrupted scan, no transaction | ORPHAN + UNREADABLE — flag for analyst |
| RC-004–011 | Referenced in SAP, absent from repo | MISSING_DOC — retrieve before billing |
| E-7702 | Expenses this cycle, no timecard | Surfaced via draft invoice flag — analyst must verify |

---

## Auto-Resolvable KPI

**Target: ≥55% of exceptions auto-resolvable**

From 7 recurring resolutions in the exception store:

| Exception | Rule applied to |
|---|---|
| EX-2025-0828 | Hotel over-cap at coastal site → approve (no alternative) |
| EX-2025-0911 | Alcohol on any receipt → reject |
| EX-2025-1003 | Missing receipt → hold 30 days, then not billable |
| EX-2025-1129 | Subcontractor invoice → apply 8% markup |
| EX-2026-0203 | Miscoded internal time → remove from invoice |
| EX-2026-0314 | Foreign currency receipt → convert at receipt-date FX |
| EX-2025-0712 | Off-hours rate (none exists) → bill at standard rate |

---

## Technical Stack

| Component | Choice |
|---|---|
| Language | Python 3.9 |
| UI | Streamlit 1.50 |
| AI | Anthropic SDK · claude-opus-4-8 · adaptive thinking · streaming |
| State | JSON files (`state/results.json`, `state/decisions.json`, `state/audit_trail.json`) |
| Data | Synthetic dataset in `shared/appendix-sample-data/` |
| Tests | Custom test runner · 37 tests · unittest + mock |

---

## Bugs Found and Fixed

| Bug | Root cause | Fix |
|---|---|---|
| `Pipeline Error: 'classification'` | `max_tokens=2048` — adaptive thinking consumed the token budget; JSON output was truncated; `_parse_json` returned `{"error": ...}` without a `classification` key; `run_pipeline` crashed on `r["classification"]` | Raised to `max_tokens=8192` (classify) and `4096` (triage); added explicit guard after JSON parse; changed `r["classification"]` to `r.get("classification")` |

---

## Constraints (from brief)

- No real client data — only synthetic data from Appendix A
- SAP is the system of record — the solution integrates, does not replace
- Any SAP update must be risk-free — no unauthorised changes
- Project Lead workflow must remain undisturbed

---

## Files

```
pod1/
├── app.py              Streamlit UI (Dashboard / Exception Queue / All Transactions / Audit Trail)
├── pipeline.py         Three-agent pipeline + state management
├── data_loader.py      Data loading utilities (CSV, markdown docs, contract, exceptions)
├── test_pipeline.py    37-test suite (data loading, rule-based, mocked Claude, state)
├── requirements.txt    anthropic, streamlit, pandas
└── state/              Runtime — audit_trail.json, decisions.json, results.json
```

**Run:**
```bash
cd pod1
export ANTHROPIC_API_KEY=...   # or load from ../.env
streamlit run app.py
```
