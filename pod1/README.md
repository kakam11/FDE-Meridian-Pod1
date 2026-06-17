# Reconciliation Agent — Demo

Agentic billing reconciliation pipeline for PRJ-NS-7421 · Northstar Civic Group · Cycle 2026-04.

## Quick start

```bash
cd pod1
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY=your-key-here
streamlit run app.py
```

Open http://localhost:8501

## What it does

1. **Ingest** — loads SAP transactions CSV, contract, backup documents (markdown), prior exceptions
2. **Extract + Match + Validate + Classify** — for each expense transaction, calls Claude (opus-4-8) to do line-level document extraction and classify as CLEAN / FLAG / EXEMPT / MISSING_DOC / ORPHAN / UNREADABLE
3. **Exception Triage** — auto-suggests resolutions for flagged items using prior recurring resolutions (filtered to this project only)
4. **Analyst Review UI** — Streamlit interface to review each exception, accept/reject/adjust the agent's suggestion, and record a decision with reason
5. **Audit Trail** — immutable append-only JSON log of all pipeline runs and analyst decisions

## State files (auto-created)

| File | Contents |
|---|---|
| `state/results.json` | Last pipeline run — all classified transactions |
| `state/decisions.json` | All analyst decisions |
| `state/audit_trail.json` | Immutable event log (pipeline runs + decisions) |

Results are cached — re-running the pipeline overwrites `results.json` but all decisions in `decisions.json` are preserved.

## Key edge cases surfaced

| Transaction | Issue |
|---|---|
| TX-0029 (RC-007) | Team dinner receipt contains alcohol — splits required |
| TX-0025 (RC-003) | Hotel folio: dinner includes complimentary wine → alcohol not reimbursable |
| TX-0044 (VI-002) | Subcontractor invoice: +8% markup missing from SAP amount |
| TX-0040 (RC-012) | Hotel over per-night cap ($310 vs $195 elsewhere) |
| TX-0043 (RC-013) | Client dinner over $90/day meal cap |
| TX-0048 (RC-015) | CAD receipt on USD project — FX conversion required |
| TX-0050 (RC-017) | Personal laundry item — not reimbursable |
| TX-0037 | No receipt, amount $67.30 — prior resolution EX-2025-1003 applies |
| ORPHAN-RC-019 | Document in repo with no SAP transaction |
| RC-004 to RC-011 | Referenced in SAP but not found in document repo |

## Architecture

```
Ingest → [Extract+Match+Validate+Classify] → Exception Triage → Analyst Review
                 (Claude opus-4-8)               (Claude opus-4-8)   (Streamlit UI)
```

All Claude calls use adaptive thinking + streaming. Results cached in `state/` as JSON.
