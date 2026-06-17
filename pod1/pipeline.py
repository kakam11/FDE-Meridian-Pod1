"""
Core reconciliation pipeline.

Agents:
  1. Ingest          — loads all data sources
  2. Extract+Match+Validate+Classify — per transaction, calls Claude to do line-level analysis
  3. Exception Triage — auto-resolves exceptions using prior recurring resolutions
  4. Audit Trail     — immutable append-only JSON log
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

import rules
from data_loader import (
    load_transactions, load_contract, load_prior_exceptions, load_all_documents,
    load_pm_instructions, load_restricted_docs,
    get_expense_transactions, get_recurring_exceptions_for_project,
    extract_backup_ref, format_transaction, format_prior_resolutions,
)

STATE_DIR = Path(__file__).parent / "state"
STATE_DIR.mkdir(exist_ok=True)

RESULTS_FILE = STATE_DIR / "results.json"
AUDIT_FILE = STATE_DIR / "audit_trail.json"
DECISIONS_FILE = STATE_DIR / "decisions.json"

PROJECT_ID = "PRJ-NS-7421"

CLEAN = "CLEAN"
FLAG = "FLAG"
EXEMPT = "EXEMPT"
ORPHAN = "ORPHAN"
UNREADABLE = "UNREADABLE"
MISSING_DOC = "MISSING_DOC"

CLASSIFY_SYSTEM = """You are a billing reconciliation analyst for a professional services firm.
You receive a SAP expense transaction, its backup document, contract rules, and prior resolution history.
You must perform line-level extraction and output ONLY a valid JSON object — no prose, no markdown fences."""

CLASSIFY_PROMPT = """## Contract Rules
{contract}

## PM Instructions & Project Notes
{pm_instructions}

## Prior Recurring Resolutions for project {project_id}
{prior_resolutions}

## SAP Transaction
{transaction}

## Backup Document
{document}

Analyse this transaction against its backup document line by line.
Return exactly this JSON structure:
{{
  "classification": "CLEAN|FLAG|EXEMPT|UNREADABLE",
  "confidence": 0.95,
  "doc_total_extracted": null,
  "doc_currency_extracted": "USD",
  "reimbursable_amount": null,
  "non_reimbursable_items": [],
  "issues": [],
  "auto_resolution": null,
  "analyst_note": ""
}}

Where:
- classification CLEAN = amounts match, no policy violation
- classification FLAG = policy violation, amount mismatch, or ambiguity needing analyst
- classification EXEMPT = per diem or under-$25 (no receipt required)
- classification UNREADABLE = document cannot be parsed

For auto_resolution (only if derivable from contract or prior resolutions above, else null):
  {{"action": "APPROVE|REJECT|ADJUST|ESCALATE", "adjusted_amount": null, "reason": "...", "based_on_prior": null}}

non_reimbursable_items: list each disallowed line with its amount, e.g. ["Alcohol - house red x2: $18.00"]
issues: list each specific problem found."""

TRIAGE_SYSTEM = """You are a billing exception triage agent. Given a flagged item and the project's prior recurring resolutions, determine if this exception can be auto-resolved. Respond with ONLY valid JSON."""

TRIAGE_PROMPT = """## Prior Recurring Resolutions for {project_id}
{prior_resolutions}

## Contract Rules (key sections)
{contract_excerpt}

## Flagged Transaction
{flagged_item}

## Agent's initial classification notes
Issues: {issues}
Analyst note: {analyst_note}

Return:
{{
  "auto_resolvable": true,
  "suggested_action": "APPROVE|REJECT|ADJUST|ESCALATE",
  "adjusted_amount": null,
  "reasoning": "...",
  "based_on_prior_exception": null,
  "confidence": 0.9,
  "requires_pl_approval": false
}}"""


# ── Claude helpers ──────────────────────────────────────────────────────────

def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _call_claude(system: str, prompt: str, max_tokens: int = 8192) -> str:
    client = _get_client()
    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    # Extract last text block (thinking blocks come first)
    for block in reversed(msg.content):
        if hasattr(block, "text"):
            return block.text
    return ""


def _parse_json(text: str) -> dict:
    """Extract JSON from Claude response, handling markdown fences."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {"error": "JSON parse failed", "raw": text[:300]}


# ── Agent 1: Ingest ─────────────────────────────────────────────────────────

def ingest() -> dict:
    transactions = load_transactions()
    expense_txns = get_expense_transactions(transactions)
    contract = load_contract()
    all_exceptions = load_prior_exceptions()
    prior_resolutions = get_recurring_exceptions_for_project(all_exceptions, PROJECT_ID)
    all_docs = load_all_documents()
    pm_instructions = load_pm_instructions()
    restricted_docs = load_restricted_docs()
    return {
        "expense_txns": expense_txns,
        "contract": contract,
        "prior_resolutions": prior_resolutions,
        "all_docs": all_docs,
        "pm_instructions": pm_instructions,
        "restricted_docs": restricted_docs,
    }


# ── Agent 2: Extract + Match + Validate + Classify ──────────────────────────

def _classify_exempt(tx: dict, reason: str, note: str) -> dict:
    return {
        "transaction_id": tx["transaction_id"],
        "classification": EXEMPT,
        "confidence": 1.0,
        "backup_ref": extract_backup_ref(tx.get("note", "")),
        "doc_total_extracted": None,
        "reimbursable_amount": float(tx["amount"]),
        "non_reimbursable_items": [],
        "issues": [reason],
        "auto_resolution": {
            "action": "APPROVE",
            "adjusted_amount": float(tx["amount"]),
            "reason": reason,
            "based_on_prior": None,
        },
        "analyst_note": reason,
    }


def _rule_result_to_dict(rule_result: rules.RuleResult, tx: dict, backup_ref: Optional[str], amount: float) -> dict:
    return {
        "transaction_id": tx["transaction_id"],
        "classification": rule_result.classification,
        "confidence": rule_result.confidence,
        "backup_ref": backup_ref,
        "doc_total_extracted": None,
        "reimbursable_amount": amount if rule_result.classification == EXEMPT else None,
        "non_reimbursable_items": rule_result.non_reimbursable_items,
        "issues": rule_result.issues,
        "auto_resolution": rule_result.auto_resolution,
        "analyst_note": rule_result.analyst_note,
    }


def classify_transaction(
    tx: dict,
    doc_content: Optional[str],
    contract: str,
    prior_resolutions: list[dict],
    pm_instructions: str = "",
) -> dict:
    note = tx.get("note", "")
    backup_ref = extract_backup_ref(note)
    amount = float(tx["amount"])

    # Rule engine: deterministic contract checks before Claude
    rule_result = rules.evaluate(tx, doc_content, contract, prior_resolutions)
    if rule_result is not None and rule_result.skip_claude:
        return _rule_result_to_dict(rule_result, tx, backup_ref, amount)

    # Build Claude prompt; if a rule fired with skip_claude=False, inject its findings
    rule_context = ""
    if rule_result is not None:
        rule_context = (
            f"\n\n## Pre-analysis Rule Engine Findings\n"
            f"Issues: {'; '.join(rule_result.issues)}\n"
            f"Suggested action: {rule_result.auto_resolution}"
        )

    prompt = CLASSIFY_PROMPT.format(
        contract=contract[:3500],
        pm_instructions=pm_instructions[:2000] if pm_instructions else "None on file.",
        project_id=PROJECT_ID,
        prior_resolutions=format_prior_resolutions(prior_resolutions),
        transaction=format_transaction(tx),
        document=(doc_content or "")[:4000],
    ) + rule_context

    try:
        raw = _call_claude(CLASSIFY_SYSTEM, prompt)
        result = _parse_json(raw)
        # Guard: if Claude's response couldn't be parsed into valid JSON with a
        # classification key, treat it as unreadable rather than propagating a KeyError
        if "classification" not in result:
            parse_error = result.get("raw", raw[:300])
            return {
                "transaction_id": tx["transaction_id"],
                "classification": UNREADABLE,
                "confidence": 0.0,
                "backup_ref": backup_ref,
                "doc_total_extracted": None,
                "reimbursable_amount": None,
                "non_reimbursable_items": [],
                "issues": [f"Claude response could not be parsed into expected JSON. Raw: {parse_error}"],
                "auto_resolution": None,
                "analyst_note": "Agent response was malformed — flag for manual review.",
            }
        result["transaction_id"] = tx["transaction_id"]
        result["backup_ref"] = backup_ref
        # Merge any rule engine findings into Claude's result
        if rule_result is not None:
            result["issues"] = list(dict.fromkeys(rule_result.issues + result.get("issues", [])))
            result["non_reimbursable_items"] = list(dict.fromkeys(
                rule_result.non_reimbursable_items + result.get("non_reimbursable_items", [])
            ))
            if not result.get("auto_resolution") and rule_result.auto_resolution:
                result["auto_resolution"] = rule_result.auto_resolution
        return result
    except Exception as e:
        return {
            "transaction_id": tx["transaction_id"],
            "classification": UNREADABLE,
            "confidence": 0.0,
            "backup_ref": backup_ref,
            "doc_total_extracted": None,
            "reimbursable_amount": None,
            "non_reimbursable_items": [],
            "issues": [f"Processing error: {e}"],
            "auto_resolution": None,
            "analyst_note": f"Claude API error: {e}",
        }


# ── Agent 3: Exception Triage (async) ───────────────────────────────────────

def triage_exception(result: dict, contract: str, prior_resolutions: list[dict]) -> dict:
    """Attempt auto-resolution for a flagged exception using prior resolutions + contract rules."""
    tx = result.get("transaction_data") or {}
    prompt = TRIAGE_PROMPT.format(
        project_id=PROJECT_ID,
        prior_resolutions=format_prior_resolutions(prior_resolutions),
        contract_excerpt=contract[:2000],
        flagged_item=format_transaction(tx) if tx else f"Orphan document: {result.get('backup_ref')}",
        issues="; ".join(result.get("issues", [])),
        analyst_note=result.get("analyst_note", ""),
    )
    try:
        raw = _call_claude(TRIAGE_SYSTEM, prompt, max_tokens=4096)
        triage = _parse_json(raw)
        return triage
    except Exception as e:
        return {
            "auto_resolvable": False,
            "suggested_action": "ESCALATE",
            "adjusted_amount": None,
            "reasoning": f"Triage error: {e}",
            "based_on_prior_exception": None,
            "confidence": 0.0,
            "requires_pl_approval": True,
        }


# ── Pipeline orchestrator ────────────────────────────────────────────────────

def run_pipeline(progress_callback=None) -> list[dict]:
    """Run the full reconciliation pipeline. progress_callback(step, total, msg) for UI updates."""
    data = ingest()
    expense_txns = data["expense_txns"]
    contract = data["contract"]
    prior_resolutions = data["prior_resolutions"]
    all_docs = data["all_docs"]
    pm_instructions = data["pm_instructions"]

    results = []
    total = len(expense_txns)

    # Agent 2: classify each expense transaction
    for i, tx in enumerate(expense_txns):
        if progress_callback:
            progress_callback(i, total, f"Classifying {tx['transaction_id']}…")

        backup_ref = extract_backup_ref(tx.get("note", ""))
        doc_content = all_docs.get(backup_ref) if backup_ref else None

        result = classify_transaction(tx, doc_content, contract, prior_resolutions, pm_instructions)
        result["transaction_data"] = tx
        result["processed_at"] = datetime.now(timezone.utc).isoformat()
        result["triage"] = None
        results.append(result)

    # Agent 3: triage exceptions (async — runs after classification, non-blocking in UI)
    flagged = [r for r in results if r.get("classification") in (FLAG, MISSING_DOC)]
    for i, r in enumerate(flagged):
        if progress_callback:
            progress_callback(total + i, total + len(flagged), f"Triaging {r['transaction_id']}…")
        # Only run triage if no auto_resolution already set by classify agent
        if not r.get("auto_resolution"):
            r["triage"] = triage_exception(r, contract, prior_resolutions)

    # Detect orphan documents — docs present in repo with no SAP transaction referencing them
    referenced_refs = {extract_backup_ref(tx.get("note", "")) for tx in expense_txns}
    for doc_ref, doc_content in all_docs.items():
        if doc_ref not in referenced_refs and doc_ref.startswith("RC-"):
            results.append({
                "transaction_id": f"ORPHAN-{doc_ref}",
                "classification": ORPHAN,
                "confidence": 1.0,
                "backup_ref": doc_ref,
                "doc_total_extracted": None,
                "reimbursable_amount": 0.0,
                "non_reimbursable_items": [],
                "issues": [f"Document {doc_ref} exists in repository but has no corresponding SAP transaction"],
                "auto_resolution": {
                    "action": "ESCALATE",
                    "adjusted_amount": None,
                    "reason": "Orphan document — verify whether a SAP transaction was omitted or this document is unrelated.",
                    "based_on_prior": None,
                },
                "triage": None,
                "analyst_note": f"Orphan: {doc_ref} in repo with no SAP match.",
                "transaction_data": None,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            })

    _save_results(results)
    _append_audit("PIPELINE_RUN", {
        "expense_transactions": total,
        "classified": len(results),
        "flagged": len(flagged),
        "orphans": len([r for r in results if r["classification"] == ORPHAN]),
    })

    return results


# ── State helpers ────────────────────────────────────────────────────────────

def _save_results(results: list[dict]):
    RESULTS_FILE.write_text(json.dumps(results, indent=2))


def load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text())
    return []


def load_decisions() -> list[dict]:
    if DECISIONS_FILE.exists():
        return json.loads(DECISIONS_FILE.read_text())
    return []


def save_decision(transaction_id: str, action: str, reason: str, analyst: str, override: bool = False, adjusted_amount: Optional[float] = None):
    decisions = load_decisions()
    event = {
        "transaction_id": transaction_id,
        "action": action,
        "adjusted_amount": adjusted_amount,
        "reason": reason,
        "analyst": analyst,
        "override": override,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    decisions.append(event)
    DECISIONS_FILE.write_text(json.dumps(decisions, indent=2))
    _append_audit("ANALYST_DECISION", event)


def _append_audit(event_type: str, payload: dict):
    trail = []
    if AUDIT_FILE.exists():
        trail = json.loads(AUDIT_FILE.read_text())
    trail.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    })
    AUDIT_FILE.write_text(json.dumps(trail, indent=2))


def load_audit_trail() -> list[dict]:
    if AUDIT_FILE.exists():
        return json.loads(AUDIT_FILE.read_text())
    return []
