"""
Test suite for the reconciliation pipeline.
Run: python3 test_pipeline.py
Covers: data loading, rule-based classification, Claude API calls, state management.
"""

import json
import os
import sys
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure pod1/ is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from data_loader import (
    load_transactions, load_contract, load_prior_exceptions, load_all_documents,
    get_expense_transactions, get_recurring_exceptions_for_project,
    extract_backup_ref, format_transaction, format_prior_resolutions,
    DATA_DIR,
)
from pipeline import (
    ingest, classify_transaction, run_pipeline,
    load_results, load_decisions, save_decision, load_audit_trail,
    _parse_json, CLEAN, FLAG, EXEMPT, ORPHAN, UNREADABLE, MISSING_DOC,
    STATE_DIR, RESULTS_FILE, AUDIT_FILE, DECISIONS_FILE,
)
import rules as rules_module


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def run(label, fn):
    try:
        fn()
        print(f"  {PASS}  {label}")
        return True
    except Exception as e:
        print(f"  {FAIL}  {label}")
        print(f"         {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


# ── Data Loading ──────────────────────────────────────────────────────────────

def test_data_dir_exists():
    assert DATA_DIR.exists(), f"Data directory missing: {DATA_DIR}"


def test_load_transactions():
    txns = load_transactions()
    assert len(txns) == 50, f"Expected 50 transactions, got {len(txns)}"
    required_cols = {"transaction_id", "type", "employee_id", "amount", "currency", "note"}
    for col in required_cols:
        assert col in txns[0], f"Missing column: {col}"


def test_expense_transactions():
    txns = load_transactions()
    expense = get_expense_transactions(txns)
    assert len(expense) == 27, f"Expected 27 expense transactions, got {len(expense)}"
    assert all(t["type"] == "EXPENSE" for t in expense)


def test_load_contract():
    contract = load_contract()
    assert "MSA-NS-2024-0418" in contract
    assert "Not reimbursable" in contract  # alcohol rule


def test_load_prior_exceptions():
    exceptions = load_prior_exceptions()
    assert len(exceptions) == 10, f"Expected 10 exceptions, got {len(exceptions)}"
    recurring = get_recurring_exceptions_for_project(exceptions, "PRJ-NS-7421")
    assert len(recurring) == 7, f"Expected 7 recurring for PRJ-NS-7421, got {len(recurring)}"


def test_cross_project_contamination_blocked():
    exceptions = load_prior_exceptions()
    recurring = get_recurring_exceptions_for_project(exceptions, "PRJ-NS-7421")
    ids = [e["exception_id"] for e in recurring]
    assert "EX-2026-0327" not in ids, "Cross-project exception EX-2026-0327 must not appear for PRJ-NS-7421"


def test_load_documents():
    docs = load_all_documents()
    assert len(docs) == 15, f"Expected 15 documents, got {len(docs)}"
    assert "RC-007" in docs, "RC-007 (team dinner with alcohol) must be present"
    assert "VI-002" in docs, "VI-002 (subcontractor) must be present"
    # Confirm missing docs are not present
    for missing in ("RC-004", "RC-005", "RC-006", "RC-008", "RC-009", "RC-010", "RC-011"):
        assert missing not in docs, f"{missing} should be absent from repo"


def test_extract_backup_ref():
    cases = [
        ("Receipt: RC-001", "RC-001"),
        ("Receipt: RC-007 (mixed: incl alcohol)", "RC-007"),
        ("Vendor invoice: VI-002 (cost). Markup +8% to be added", "VI-002"),
        ("Mileage log: ML-001", "ML-001"),
        ("No receipt - under 25 USD threshold", None),
        ("Receipt: missing", None),
        ("No receipt required (per diem)", None),
        ("", None),
    ]
    for note, expected in cases:
        got = extract_backup_ref(note)
        assert got == expected, f"extract_backup_ref({note!r}) = {got!r}, want {expected!r}"


def test_format_transaction():
    txns = get_expense_transactions(load_transactions())
    tx = txns[0]
    formatted = format_transaction(tx)
    assert "Transaction ID:" in formatted
    assert "Amount:" in formatted


def test_format_prior_resolutions():
    exceptions = load_prior_exceptions()
    recurring = get_recurring_exceptions_for_project(exceptions, "PRJ-NS-7421")
    text = format_prior_resolutions(recurring)
    assert "EX-2025-0911" in text  # alcohol rejection rule
    assert "EX-2025-1129" in text  # subcontractor markup rule


# ── JSON Parsing ──────────────────────────────────────────────────────────────

def test_parse_json_clean():
    text = '{"classification": "CLEAN", "confidence": 0.95}'
    result = _parse_json(text)
    assert result["classification"] == "CLEAN"


def test_parse_json_with_fences():
    text = '```json\n{"classification": "FLAG", "issues": ["amount mismatch"]}\n```'
    result = _parse_json(text)
    assert result["classification"] == "FLAG"
    assert len(result["issues"]) == 1


def test_parse_json_with_preamble():
    text = 'Here is the analysis:\n{"classification": "EXEMPT", "confidence": 1.0}'
    result = _parse_json(text)
    assert result["classification"] == "EXEMPT"


def test_parse_json_invalid_returns_error():
    result = _parse_json("not json at all")
    assert "error" in result


# ── Rule-based Classification (no API calls) ──────────────────────────────────

def _get_tx(tx_id):
    return next(t for t in get_expense_transactions(load_transactions()) if t["transaction_id"] == tx_id)


def test_classify_per_diem_exempt():
    tx = _get_tx("TX-2026-04-0041")  # per diem
    result = classify_transaction(tx, None, load_contract(), [])
    assert result["classification"] == EXEMPT, f"Per diem should be EXEMPT, got {result['classification']}"
    assert result["auto_resolution"]["action"] == "APPROVE"


def test_classify_second_per_diem_exempt():
    tx = _get_tx("TX-2026-04-0042")  # second per diem
    result = classify_transaction(tx, None, load_contract(), [])
    assert result["classification"] == EXEMPT


def test_classify_under_25_exempt():
    tx = _get_tx("TX-2026-04-0030")  # site parking $18, no receipt
    result = classify_transaction(tx, None, load_contract(), [])
    assert result["classification"] == EXEMPT, f"Under-$25 should be EXEMPT, got {result['classification']}"


def test_classify_missing_doc_referenced():
    tx = _get_tx("TX-2026-04-0026")  # RC-004 not in repo
    docs = load_all_documents()
    doc_content = docs.get("RC-004")  # None
    assert doc_content is None
    result = classify_transaction(tx, None, load_contract(), [])
    assert result["classification"] == MISSING_DOC
    assert result["backup_ref"] == "RC-004"


def test_classify_missing_receipt_no_ref():
    tx = _get_tx("TX-2026-04-0037")  # "Receipt: missing"
    result = classify_transaction(tx, None, load_contract(), [])
    assert result["classification"] == FLAG
    assert result["auto_resolution"] is not None
    assert result["auto_resolution"]["action"] == "REJECT"
    assert "EX-2025-1003" in result["auto_resolution"].get("based_on_prior", "")


def test_classify_all_missing_doc_txns():
    txns = get_expense_transactions(load_transactions())
    docs = load_all_documents()
    contract = load_contract()
    missing_refs = {"RC-004", "RC-005", "RC-006", "RC-008", "RC-009", "RC-010", "RC-011"}
    for tx in txns:
        ref = extract_backup_ref(tx.get("note", ""))
        if ref in missing_refs:
            result = classify_transaction(tx, None, contract, [])
            assert result["classification"] == MISSING_DOC, \
                f"{tx['transaction_id']} with missing {ref} should be MISSING_DOC, got {result['classification']}"


# ── Claude API (mocked) ───────────────────────────────────────────────────────

def _make_mock_client(response_text: str):
    """Build a mock anthropic client that returns response_text."""
    mock_block = MagicMock()
    mock_block.text = response_text
    mock_block.type = "text"

    mock_msg = MagicMock()
    mock_msg.content = [mock_block]

    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_stream.get_final_message = MagicMock(return_value=mock_msg)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = mock_stream
    return mock_client


def test_classify_with_mock_claude_clean():
    claude_response = json.dumps({
        "classification": "CLEAN",
        "confidence": 0.98,
        "doc_total_extracted": 412.80,
        "doc_currency_extracted": "USD",
        "reimbursable_amount": 412.80,
        "non_reimbursable_items": [],
        "issues": [],
        "auto_resolution": {"action": "APPROVE", "adjusted_amount": 412.80, "reason": "Amount matches receipt.", "based_on_prior": None},
        "analyst_note": "Clean flight receipt."
    })
    with patch("pipeline.anthropic.Anthropic", return_value=_make_mock_client(claude_response)):
        tx = _get_tx("TX-2026-04-0022")  # RC-001 flight
        docs = load_all_documents()
        result = classify_transaction(tx, docs["RC-001"], load_contract(), [])
    assert result["classification"] == CLEAN
    assert result["backup_ref"] == "RC-001"
    assert result["doc_total_extracted"] == 412.80


def test_classify_with_mock_claude_alcohol_flag():
    claude_response = json.dumps({
        "classification": "FLAG",
        "confidence": 0.97,
        "doc_total_extracted": 126.85,
        "doc_currency_extracted": "USD",
        "reimbursable_amount": 19.50,
        "non_reimbursable_items": ["Alcohol - House red x2: $18.00"],
        "issues": [
            "Receipt total (126.85) does not match SAP amount (46.20).",
            "Alcohol charges present — not reimbursable per contract.",
        ],
        "auto_resolution": {"action": "REJECT", "adjusted_amount": 0, "reason": "Alcohol not reimbursable. EX-2025-0911.", "based_on_prior": "EX-2025-0911"},
        "analyst_note": "Mixed receipt with alcohol. Reject alcohol portion."
    })
    with patch("pipeline.anthropic.Anthropic", return_value=_make_mock_client(claude_response)):
        tx = _get_tx("TX-2026-04-0029")  # RC-007 team dinner
        docs = load_all_documents()
        result = classify_transaction(tx, docs["RC-007"], load_contract(), [])
    assert result["classification"] == FLAG
    assert len(result["non_reimbursable_items"]) > 0
    assert result["auto_resolution"]["action"] == "REJECT"


def test_classify_with_mock_claude_subcontractor_markup():
    claude_response = json.dumps({
        "classification": "FLAG",
        "confidence": 0.99,
        "doc_total_extracted": 2400.00,
        "doc_currency_extracted": "USD",
        "reimbursable_amount": 2592.00,
        "non_reimbursable_items": [],
        "issues": ["Subcontractor markup (+8%) not applied. SAP records cost only (2400.00). Invoice total should be 2592.00."],
        "auto_resolution": {"action": "ADJUST", "adjusted_amount": 2592.00, "reason": "Apply 8% subcontractor markup per EX-2025-1129.", "based_on_prior": "EX-2025-1129"},
        "analyst_note": "VI-002: cost 2400 + 8% = 2592."
    })
    with patch("pipeline.anthropic.Anthropic", return_value=_make_mock_client(claude_response)):
        tx = _get_tx("TX-2026-04-0044")  # VI-002 subcontractor
        docs = load_all_documents()
        result = classify_transaction(tx, docs["VI-002"], load_contract(), [])
    assert result["classification"] == FLAG
    assert result["auto_resolution"]["adjusted_amount"] == 2592.00
    assert result["auto_resolution"]["based_on_prior"] == "EX-2025-1129"


def test_classify_with_mock_claude_fx():
    claude_response = json.dumps({
        "classification": "FLAG",
        "confidence": 0.95,
        "doc_total_extracted": 52.00,
        "doc_currency_extracted": "CAD",
        "reimbursable_amount": None,
        "non_reimbursable_items": [],
        "issues": ["Receipt in CAD on a USD-billed project. FX conversion required before billing."],
        "auto_resolution": {"action": "ADJUST", "adjusted_amount": None, "reason": "Convert at receipt-date FX per EX-2026-0314.", "based_on_prior": "EX-2026-0314"},
        "analyst_note": "CAD 52.00 needs USD conversion at 2026-04-02 rate."
    })
    with patch("pipeline.anthropic.Anthropic", return_value=_make_mock_client(claude_response)):
        tx = _get_tx("TX-2026-04-0048")  # RC-015 CAD receipt
        docs = load_all_documents()
        result = classify_transaction(tx, docs["RC-015"], load_contract(), [])
    assert result["classification"] == FLAG
    assert result["doc_currency_extracted"] == "CAD"
    assert "EX-2026-0314" in result["auto_resolution"].get("based_on_prior", "")


def test_classify_claude_api_error_returns_unreadable():
    with patch("pipeline._get_client", side_effect=Exception("API unavailable")):
        tx = _get_tx("TX-2026-04-0022")
        docs = load_all_documents()
        result = classify_transaction(tx, docs["RC-001"], load_contract(), [])
    assert result["classification"] == UNREADABLE
    assert "API unavailable" in result["issues"][0]


def test_classify_malformed_claude_response_returns_unreadable():
    """When Claude returns non-JSON text (e.g. truncated by token limit), must not raise KeyError."""
    with patch("pipeline._call_claude", return_value="Sorry, I cannot classify this."):
        tx = _get_tx("TX-2026-04-0022")
        docs = load_all_documents()
        result = classify_transaction(tx, docs["RC-001"], load_contract(), [])
    assert result["classification"] == UNREADABLE, \
        f"Malformed response should yield UNREADABLE, got {result['classification']}"
    assert "classification" in result


def test_run_pipeline_survives_malformed_claude_response():
    """Pipeline must complete even if some Claude calls return garbage."""
    call_count = 0

    def alternating_response(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count % 3 == 0:
            return "Not valid JSON at all"
        return _mock_classify_json()

    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline._call_claude", side_effect=alternating_response):
        results = run_pipeline()

    assert len([r for r in results if not r["transaction_id"].startswith("ORPHAN-")]) == 27
    # All results must have a classification key — no KeyError
    for r in results:
        assert "classification" in r, f"{r['transaction_id']} missing classification key"


# ── Full Pipeline (mocked Claude) ─────────────────────────────────────────────

def _mock_classify_json():
    return json.dumps({
        "classification": "CLEAN",
        "confidence": 0.95,
        "doc_total_extracted": 100.0,
        "doc_currency_extracted": "USD",
        "reimbursable_amount": 100.0,
        "non_reimbursable_items": [],
        "issues": [],
        "auto_resolution": None,
        "analyst_note": "Mock clean result."
    })


def test_run_pipeline_returns_all_expense_txns():
    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline.anthropic.Anthropic", return_value=mock_client):
        results = run_pipeline()
    # 27 expense txns + at least 1 orphan (RC-019)
    tx_results = [r for r in results if not r["transaction_id"].startswith("ORPHAN-")]
    assert len(tx_results) == 27, f"Expected 27 transaction results, got {len(tx_results)}"


def test_run_pipeline_detects_orphan_rc019():
    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline.anthropic.Anthropic", return_value=mock_client):
        results = run_pipeline()
    orphans = [r for r in results if r["classification"] == ORPHAN]
    orphan_refs = [r["backup_ref"] for r in orphans]
    assert "RC-019" in orphan_refs, f"RC-019 orphan not detected. Orphans: {orphan_refs}"


def test_run_pipeline_exempt_classifications():
    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline.anthropic.Anthropic", return_value=mock_client):
        results = run_pipeline()
    result_map = {r["transaction_id"]: r for r in results}
    # Per diems
    assert result_map["TX-2026-04-0041"]["classification"] == EXEMPT
    assert result_map["TX-2026-04-0042"]["classification"] == EXEMPT
    # Under-$25
    assert result_map["TX-2026-04-0030"]["classification"] == EXEMPT


def test_run_pipeline_missing_docs_flagged():
    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline.anthropic.Anthropic", return_value=mock_client):
        results = run_pipeline()
    result_map = {r["transaction_id"]: r for r in results}
    for tx_id, expected_ref in [
        ("TX-2026-04-0026", "RC-004"),
        ("TX-2026-04-0027", "RC-005"),
        ("TX-2026-04-0028", "RC-006"),
    ]:
        r = result_map[tx_id]
        assert r["classification"] == MISSING_DOC, \
            f"{tx_id} should be MISSING_DOC, got {r['classification']}"
        assert r["backup_ref"] == expected_ref


def test_run_pipeline_no_cross_project_resolutions():
    """EX-2026-0327 (PRJ-OTHER-9912) must never appear in triage output."""
    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline.anthropic.Anthropic", return_value=mock_client):
        results = run_pipeline()
    for r in results:
        triage = r.get("triage") or {}
        based_on = triage.get("based_on_prior_exception") or ""
        res = r.get("auto_resolution") or {}
        based_on2 = res.get("based_on_prior") or ""
        assert "EX-2026-0327" not in based_on and "EX-2026-0327" not in based_on2, \
            f"Cross-project exception used in {r['transaction_id']}"


def test_run_pipeline_audit_event_written():
    mock_client = _make_mock_client(_mock_classify_json())
    with patch("pipeline.anthropic.Anthropic", return_value=mock_client):
        run_pipeline()
    trail = load_audit_trail()
    run_events = [e for e in trail if e["event_type"] == "PIPELINE_RUN"]
    assert len(run_events) >= 1


# ── State Management ──────────────────────────────────────────────────────────

def test_save_and_load_decision():
    save_decision("TX-TEST-001", "APPROVE", "Looks fine", "TestAnalyst", False, 100.0)
    decisions = load_decisions()
    match = next((d for d in decisions if d["transaction_id"] == "TX-TEST-001"), None)
    assert match is not None, "Saved decision not found"
    assert match["action"] == "APPROVE"
    assert match["analyst"] == "TestAnalyst"
    assert match["adjusted_amount"] == 100.0


def test_decision_written_to_audit():
    save_decision("TX-TEST-002", "REJECT", "No receipt", "TestAnalyst2", True)
    trail = load_audit_trail()
    decision_events = [e for e in trail if e["event_type"] == "ANALYST_DECISION"
                       and e["payload"].get("transaction_id") == "TX-TEST-002"]
    assert len(decision_events) >= 1
    assert decision_events[-1]["payload"]["override"] is True


def test_audit_trail_is_append_only():
    before = len(load_audit_trail())
    save_decision("TX-TEST-003", "ESCALATE", "Need PL sign-off", "TestAnalyst3")
    after = len(load_audit_trail())
    assert after == before + 1, "Audit trail should grow by 1"


def test_load_results_empty_when_no_file():
    # Move results file temporarily
    temp = RESULTS_FILE.with_suffix(".bak")
    moved = False
    if RESULTS_FILE.exists():
        RESULTS_FILE.rename(temp)
        moved = True
    try:
        results = load_results()
        assert results == []
    finally:
        if moved:
            temp.rename(RESULTS_FILE)


# ── Rules Engine ─────────────────────────────────────────────────────────────

def test_rule_per_diem_fires():
    tx = _get_tx("TX-2026-04-0041")
    result = rules_module.evaluate(tx, None, "", [])
    assert result is not None
    assert result.classification == rules_module.EXEMPT
    assert result.skip_claude is True


def test_rule_under_threshold_fires():
    tx = _get_tx("TX-2026-04-0030")
    result = rules_module.evaluate(tx, None, "", [])
    assert result is not None
    assert result.classification == rules_module.EXEMPT


def test_rule_missing_doc_fires():
    tx = _get_tx("TX-2026-04-0026")  # references RC-004, absent from repo
    result = rules_module.evaluate(tx, None, "", [])
    assert result is not None
    assert result.classification == rules_module.MISSING_DOC


def test_rule_alcohol_fires():
    tx = {"transaction_id": "TX-TEST", "description": "Team dinner", "amount": "126.85",
          "currency": "USD", "note": "Receipt: RC-007", "employee_id": "E001"}
    result = rules_module.rule_alcohol(tx, "House red x2 $18.00\nFood $108.85", "", [])
    assert result is not None
    assert result.classification == rules_module.FLAG
    assert result.skip_claude is False
    assert result.auto_resolution["action"] == "REJECT"
    assert result.auto_resolution["based_on_prior"] == "EX-2025-0911"


def test_rule_personal_items_fires():
    tx = {"transaction_id": "TX-TEST", "description": "Personal laundry", "amount": "22.00",
          "currency": "USD", "note": "Receipt: RC-017", "employee_id": "E001"}
    result = rules_module.rule_personal_items(tx, "Laundry service $22.00", "", [])
    assert result is not None
    assert result.classification == rules_module.FLAG
    assert result.auto_resolution["action"] == "REJECT"


def test_rule_subcontractor_markup_does_not_fire_when_mentioned():
    # Note says "Markup +8%" → rule skips
    tx = {"transaction_id": "TX-TEST", "description": "Subcontractor", "amount": "2400.00",
          "currency": "USD", "note": "Vendor invoice: VI-002. Markup +8% per contract.", "employee_id": "E001"}
    result = rules_module.rule_subcontractor_markup(tx, "Invoice total $2,400.00", "", [])
    assert result is None


def test_rule_subcontractor_markup_fires_when_missing():
    tx = {"transaction_id": "TX-TEST", "description": "Subcontractor", "amount": "2400.00",
          "currency": "USD", "note": "Vendor invoice: VI-002", "employee_id": "E001"}
    result = rules_module.rule_subcontractor_markup(tx, "Subcontractor services. Total $2,400.00", "", [])
    assert result is not None
    assert result.classification == rules_module.FLAG
    assert result.auto_resolution["action"] == "ADJUST"
    assert result.auto_resolution["adjusted_amount"] == 2592.00
    assert result.auto_resolution["based_on_prior"] == "EX-2025-1129"


def test_rule_foreign_currency_fires():
    tx = {"transaction_id": "TX-TEST", "description": "Travel expense", "amount": "38.50",
          "currency": "USD", "note": "Receipt: RC-015", "employee_id": "E001"}
    result = rules_module.rule_foreign_currency(tx, "Receipt\nTotal: CAD 52.00", "", [])
    assert result is not None
    assert result.classification == rules_module.FLAG
    assert result.skip_claude is False
    assert result.auto_resolution["based_on_prior"] == "EX-2026-0314"


def test_rule_meal_cap_fires_over_limit():
    tx = {"transaction_id": "TX-TEST", "description": "Client dinner", "amount": "118.00",
          "currency": "USD", "note": "Receipt: RC-013", "employee_id": "E001"}
    result = rules_module.rule_meal_cap(tx, "Restaurant receipt Total $118.00", "", [])
    assert result is not None
    assert result.classification == rules_module.FLAG
    assert result.skip_claude is False


def test_rule_meal_cap_does_not_fire_under_limit():
    tx = {"transaction_id": "TX-TEST", "description": "Lunch", "amount": "45.00",
          "currency": "USD", "note": "", "employee_id": "E001"}
    result = rules_module.rule_meal_cap(tx, "Restaurant receipt Total $45.00", "", [])
    assert result is None


def test_rule_lodging_cap_fires_coastal_with_pre_approval():
    tx = {"transaction_id": "TX-TEST", "description": "Hotel stay", "amount": "310.00",
          "currency": "USD", "note": "Receipt: RC-012", "employee_id": "E001"}
    result = rules_module.rule_lodging_cap(tx, "Hotel receipt Location: coastal point B Rate: $310/night", "", [])
    assert result is not None
    assert result.auto_resolution["action"] == "APPROVE"
    assert result.auto_resolution["based_on_prior"] == "EX-2025-0828"


def test_evaluate_returns_none_for_ambiguous():
    """Clean flight with receipt — no rules should fire; must escalate to Claude."""
    tx = _get_tx("TX-2026-04-0022")  # RC-001 flight
    docs = load_all_documents()
    result = rules_module.evaluate(tx, docs["RC-001"], load_contract(), [])
    assert result is None, f"Clean flight should escalate to Claude, but rule fired: {result}"


def test_evaluate_skip_claude_true_does_not_call_api():
    """EXEMPT result from rule engine must not trigger a Claude API call."""
    tx = _get_tx("TX-2026-04-0041")  # per diem
    with patch("pipeline._call_claude") as mock_claude:
        result = classify_transaction(tx, None, load_contract(), [])
    mock_claude.assert_not_called()
    assert result["classification"] == EXEMPT


# ── Runner ────────────────────────────────────────────────────────────────────

GROUPS = {
    "Data Loading": [
        ("DATA_DIR exists", test_data_dir_exists),
        ("load_transactions() returns 50 rows", test_load_transactions),
        ("get_expense_transactions() returns 27", test_expense_transactions),
        ("load_contract() contains key rules", test_load_contract),
        ("load_prior_exceptions() — 10 total, 7 recurring", test_load_prior_exceptions),
        ("cross-project contamination blocked (EX-2026-0327)", test_cross_project_contamination_blocked),
        ("load_all_documents() — 15 docs, missing 7 confirmed", test_load_documents),
        ("extract_backup_ref() — all patterns", test_extract_backup_ref),
        ("format_transaction()", test_format_transaction),
        ("format_prior_resolutions()", test_format_prior_resolutions),
    ],
    "JSON Parsing": [
        ("clean JSON", test_parse_json_clean),
        ("JSON with markdown fences", test_parse_json_with_fences),
        ("JSON with preamble text", test_parse_json_with_preamble),
        ("invalid JSON returns error dict", test_parse_json_invalid_returns_error),
    ],
    "Rule-Based Classification (no API)": [
        ("per diem TX-0041 → EXEMPT", test_classify_per_diem_exempt),
        ("per diem TX-0042 → EXEMPT", test_classify_second_per_diem_exempt),
        ("under-$25 TX-0030 → EXEMPT", test_classify_under_25_exempt),
        ("missing doc (RC-004) TX-0026 → MISSING_DOC", test_classify_missing_doc_referenced),
        ("missing receipt TX-0037 → FLAG + REJECT auto-res", test_classify_missing_receipt_no_ref),
        ("all 7 missing-doc transactions → MISSING_DOC", test_classify_all_missing_doc_txns),
    ],
    "Claude API (mocked)": [
        ("clean flight receipt → CLEAN", test_classify_with_mock_claude_clean),
        ("alcohol in RC-007 → FLAG + REJECT", test_classify_with_mock_claude_alcohol_flag),
        ("subcontractor markup VI-002 → FLAG + ADJUST 2592", test_classify_with_mock_claude_subcontractor_markup),
        ("CAD receipt RC-015 → FLAG + FX note", test_classify_with_mock_claude_fx),
        ("API error → UNREADABLE", test_classify_claude_api_error_returns_unreadable),
        ("malformed Claude response → UNREADABLE (not KeyError)", test_classify_malformed_claude_response_returns_unreadable),
        ("pipeline survives mixed malformed/valid responses", test_run_pipeline_survives_malformed_claude_response),
    ],
    "Full Pipeline (mocked Claude)": [
        ("returns all 27 expense transactions", test_run_pipeline_returns_all_expense_txns),
        ("detects RC-019 orphan", test_run_pipeline_detects_orphan_rc019),
        ("per diem + under-$25 classified EXEMPT", test_run_pipeline_exempt_classifications),
        ("missing docs flagged MISSING_DOC", test_run_pipeline_missing_docs_flagged),
        ("no cross-project resolutions in output", test_run_pipeline_no_cross_project_resolutions),
        ("audit event written on run", test_run_pipeline_audit_event_written),
    ],
    "Rules Engine": [
        ("per diem rule fires → EXEMPT, skip_claude=True", test_rule_per_diem_fires),
        ("under-$25 rule fires → EXEMPT", test_rule_under_threshold_fires),
        ("missing doc rule fires → MISSING_DOC", test_rule_missing_doc_fires),
        ("alcohol rule fires → FLAG, skip_claude=False, REJECT auto-res", test_rule_alcohol_fires),
        ("personal item rule fires → FLAG + REJECT", test_rule_personal_items_fires),
        ("subcontractor markup rule skips when markup mentioned", test_rule_subcontractor_markup_does_not_fire_when_mentioned),
        ("subcontractor markup rule fires → FLAG + ADJUST 2592", test_rule_subcontractor_markup_fires_when_missing),
        ("foreign currency rule fires → FLAG, skip_claude=False", test_rule_foreign_currency_fires),
        ("meal cap rule fires when over $90", test_rule_meal_cap_fires_over_limit),
        ("meal cap rule does not fire under $90", test_rule_meal_cap_does_not_fire_under_limit),
        ("lodging cap fires at coastal site → APPROVE (EX-2025-0828)", test_rule_lodging_cap_fires_coastal_with_pre_approval),
        ("evaluate() returns None for ambiguous transaction", test_evaluate_returns_none_for_ambiguous),
        ("skip_claude=True → Claude API not called", test_evaluate_skip_claude_true_does_not_call_api),
    ],
    "State Management": [
        ("save and load decision", test_save_and_load_decision),
        ("decision written to audit trail", test_decision_written_to_audit),
        ("audit trail is append-only", test_audit_trail_is_append_only),
        ("load_results() returns [] when no file", test_load_results_empty_when_no_file),
    ],
}


def main():
    print("\n=== Reconciliation Agent Test Suite ===\n")
    total_pass = total_fail = 0
    for group, tests in GROUPS.items():
        print(f"▸ {group}")
        for label, fn in tests:
            if run(label, fn):
                total_pass += 1
            else:
                total_fail += 1
        print()

    print("=" * 40)
    print(f"Results: {total_pass} passed, {total_fail} failed")
    if total_fail > 0:
        print("\nFailing tests need fixes before demo.")
        sys.exit(1)
    else:
        print("\nAll tests passed. App is ready to demo.")


if __name__ == "__main__":
    main()
