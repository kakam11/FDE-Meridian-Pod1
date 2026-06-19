"""
Deterministic rule engine for billing reconciliation.

Rules derived from MSA-NS-2024-0418 §3–§4 and prior recurring exceptions for PRJ-NS-7421.
Rules run before Claude. If a rule fires with skip_claude=True the Claude API call is
skipped entirely. skip_claude=False means the rule detected a probable issue but wants
Claude to do line-level verification — the rule's findings are merged into the final result.

Evaluation: rules run in order; first match wins.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_RULESET_FILE = Path(__file__).parent / "state" / "ruleset.json"


def _get_ruleset() -> Optional[dict]:
    """Load ruleset from file if it exists. No caching — file is small."""
    if _RULESET_FILE.exists():
        try:
            return json.loads(_RULESET_FILE.read_text())
        except Exception:
            pass
    return None


def _cap(name: str, default: float) -> float:
    rs = _get_ruleset()
    if rs:
        return float(rs.get("caps", {}).get(name, default))
    return default


def _terms(name: str, default: list) -> list:
    rs = _get_ruleset()
    if rs:
        return rs.get(name, default)
    return default

# Classification constants (mirrors pipeline.py — no import to avoid circular dep)
CLEAN = "CLEAN"
FLAG = "FLAG"
EXEMPT = "EXEMPT"
MISSING_DOC = "MISSING_DOC"

# Contract caps §4 MSA-NS-2024-0418
MEAL_CAP_USD = 90.0
LODGING_CAP_MAJOR_METRO_USD = 275.0
LODGING_CAP_ELSEWHERE_USD = 195.0
PER_DIEM_USD = 65.0
RECEIPT_THRESHOLD_USD = 25.0
SUBCONTRACTOR_MARKUP_PCT = 0.08

ALCOHOL_TERMS = [
    "wine", "beer", "spirits", "alcohol", "liquor",
    "house red", "house white", "lager", "ale", "cocktail",
    "champagne", "prosecco", "cider", "whisky", "whiskey",
    "gin", "vodka", "rum",
]

PERSONAL_TERMS = [
    "laundry", "dry cleaning", "personal item", "toiletries",
    "clothing", "haircut", "spa", "gym",
]

LODGING_TERMS = ["hotel", "lodging", "accommodation", "motel", "inn", "resort"]
MEAL_TERMS = ["meal", "dinner", "lunch", "breakfast", "restaurant", "food", "dining"]
FOREIGN_CURRENCIES = ["CAD", "EUR", "GBP", "AUD", "MXN", "C$", "€", "£", "A$"]


@dataclass
class RuleResult:
    classification: str
    confidence: float
    issues: list[str]
    non_reimbursable_items: list[str] = field(default_factory=list)
    auto_resolution: Optional[dict] = None
    analyst_note: str = ""
    skip_claude: bool = True  # False = still run Claude but pass rule context


# ── Pre-document rules (no doc_content needed) ──────────────────────────────

def rule_per_diem(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Per diem: flat rate no receipt required per contract §4."""
    desc = tx.get("description", "").lower()
    note = tx.get("note", "").lower()
    if "per diem" in desc or "no receipt required (per diem)" in note:
        amount = float(tx.get("amount", 0))
        rate = _cap("per_diem_usd", PER_DIEM_USD)
        return RuleResult(
            classification=EXEMPT,
            confidence=1.0,
            issues=["Per diem — no receipt required per contract §4"],
            auto_resolution={
                "action": "APPROVE",
                "adjusted_amount": amount,
                "reason": f"Per diem ${rate:.0f}/day — no receipt required per contract §4",
                "based_on_prior": None,
            },
            analyst_note="Per diem — auto-approved per contract §4.",
        )
    return None


def rule_under_threshold(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Expenses under the receipt threshold do not require a receipt per contract §4."""
    note = tx.get("note", "").lower()
    amount = float(tx.get("amount", 0))
    backup_ref = _extract_ref(tx.get("note", ""))
    threshold = _cap("receipt_threshold_usd", RECEIPT_THRESHOLD_USD)
    if amount < threshold and not backup_ref and "no receipt" in note:
        return RuleResult(
            classification=EXEMPT,
            confidence=1.0,
            issues=[f"Under ${threshold:.0f} threshold — receipt not required per contract §4"],
            auto_resolution={
                "action": "APPROVE",
                "adjusted_amount": amount,
                "reason": f"Under ${threshold:.0f} — no receipt required per contract §4",
                "based_on_prior": None,
            },
            analyst_note=f"Under ${threshold:.0f} threshold — auto-approved.",
        )
    return None


def rule_missing_doc(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Document referenced in SAP note but not present in document repository."""
    backup_ref = _extract_ref(tx.get("note", ""))
    if backup_ref and not doc_content:
        return RuleResult(
            classification=MISSING_DOC,
            confidence=0.95,
            issues=[f"Backup document {backup_ref} referenced in SAP but not found in repository"],
            analyst_note=f"Retrieve {backup_ref} before billing.",
        )
    return None


def rule_no_receipt_no_ref(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """No backup reference and no document — FLAG unless amount is below threshold."""
    backup_ref = _extract_ref(tx.get("note", ""))
    amount = float(tx.get("amount", 0))
    note = tx.get("note", "")
    if not backup_ref and not doc_content and amount >= _cap("receipt_threshold_usd", RECEIPT_THRESHOLD_USD):
        note_says_missing = "missing" in note.lower()
        return RuleResult(
            classification=FLAG,
            confidence=0.95,
            issues=["No backup document and no backup reference in transaction note"],
            auto_resolution={
                "action": "REJECT",
                "adjusted_amount": 0.0,
                "reason": "No receipt on file. Per EX-2025-1003: not billable after 30 days without receipt.",
                "based_on_prior": "EX-2025-1003",
            } if note_says_missing else None,
            analyst_note="No receipt available — obtain or reject.",
        )
    return None


# ── Post-document rules (require doc_content) ───────────────────────────────

def _word_match(terms: list[str], text: str) -> list[str]:
    """Return terms found in text using word-boundary matching (avoids substring false positives)."""
    text_lower = text.lower()
    return [t for t in terms if re.search(r'\b' + re.escape(t) + r'\b', text_lower)]


def rule_alcohol(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Alcohol is not reimbursable under any circumstance per contract §4."""
    if not doc_content:
        return None
    found = _word_match(_terms("alcohol_terms", ALCOHOL_TERMS), doc_content)
    if not found:
        return None
    items = _extract_alcohol_lines(doc_content)
    return RuleResult(
        classification=FLAG,
        confidence=0.98,
        issues=[f"Alcohol detected in document: {', '.join(sorted(found))}"],
        non_reimbursable_items=items or [f"Alcohol ({', '.join(sorted(found))}) — amount embedded in total"],
        auto_resolution={
            "action": "REJECT",
            "adjusted_amount": None,
            "reason": "Alcohol not reimbursable per contract §4. Per EX-2025-0911: reject regardless of receipt total.",
            "based_on_prior": "EX-2025-0911",
        },
        analyst_note="Alcohol items detected — reject per standing rule EX-2025-0911.",
        skip_claude=False,  # Claude still extracts exact amounts and all line items
    )


def rule_personal_items(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Personal items are not reimbursable per contract §4."""
    sources = " ".join(filter(None, [doc_content or "", tx.get("description", "")]))
    found = _word_match(_terms("personal_terms", PERSONAL_TERMS), sources)
    if not found:
        return None
    return RuleResult(
        classification=FLAG,
        confidence=0.95,
        issues=[f"Personal item detected: {', '.join(sorted(set(found)))} — not reimbursable per contract §4"],
        auto_resolution={
            "action": "REJECT",
            "adjusted_amount": 0.0,
            "reason": "Personal item — not reimbursable per contract §4.",
            "based_on_prior": None,
        },
        analyst_note="Personal item — reject per contract §4.",
    )


def rule_subcontractor_markup(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Subcontractor invoices must include 8% markup per contract §3."""
    if not doc_content:
        return None
    backup_ref = _extract_ref(tx.get("note", ""))
    if not (backup_ref and backup_ref.startswith("VI-")):
        return None
    markup_pct = _cap("subcontractor_markup_pct", SUBCONTRACTOR_MARKUP_PCT)
    markup_mentioned = any(kw in (doc_content + tx.get("note", "")).lower() for kw in ["markup", "+8%", "8%", "plus 8"])
    if not markup_mentioned:
        amount = float(tx.get("amount", 0))
        adjusted = round(amount * (1 + markup_pct), 2)
        pct_display = f"{markup_pct * 100:.0f}%"
        return RuleResult(
            classification=FLAG,
            confidence=0.90,
            issues=[f"Subcontractor invoice — {pct_display} markup required per contract §3 may not be applied"],
            auto_resolution={
                "action": "ADJUST",
                "adjusted_amount": adjusted,
                "reason": f"Apply {pct_display} markup per contract §3. ${amount:.2f} → ${adjusted:.2f}. Per EX-2025-1129.",
                "based_on_prior": "EX-2025-1129",
            },
            analyst_note=f"Subcontractor markup check: ${amount:.2f} should be ${adjusted:.2f} (+{pct_display}).",
            skip_claude=False,
        )
    return None


def rule_foreign_currency(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Foreign currency receipts must be converted at receipt-date FX per EX-2026-0314."""
    if not doc_content:
        return None
    if tx.get("currency", "USD").upper() != "USD":
        return None
    doc_upper = doc_content.upper()
    found = [c for c in FOREIGN_CURRENCIES if c in doc_upper]
    if not found:
        return None
    return RuleResult(
        classification=FLAG,
        confidence=0.92,
        issues=[f"Document contains foreign currency ({', '.join(found)}) on a USD project"],
        auto_resolution={
            "action": "ADJUST",
            "adjusted_amount": None,
            "reason": "Convert at receipt-date FX rate. Per EX-2026-0314: standard practice confirmed by PL.",
            "based_on_prior": "EX-2026-0314",
        },
        analyst_note="Foreign currency receipt — apply receipt-date FX conversion per EX-2026-0314.",
        skip_claude=False,  # Claude extracts exact amounts and conversion
    )


def rule_meal_cap(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Meal expenses over $90/day cap per contract §4."""
    if not doc_content:
        return None
    if not any(kw in tx.get("description", "").lower() for kw in MEAL_TERMS):
        return None
    amount = float(tx.get("amount", 0))
    meal_cap = _cap("meal_per_day_usd", MEAL_CAP_USD)
    if amount > meal_cap:
        return RuleResult(
            classification=FLAG,
            confidence=0.88,
            issues=[f"Meal expense ${amount:.2f} exceeds ${meal_cap:.0f}/day cap per contract §4"],
            analyst_note=f"Meal over cap: ${amount:.2f} vs ${meal_cap:.0f} limit. Check for PL approval.",
            skip_claude=False,
        )
    return None


def rule_lodging_cap(tx: dict, doc_content, contract, prior_resolutions) -> Optional[RuleResult]:
    """Lodging over cap per contract §4. Coastal sites have standing PL approval (EX-2025-0828)."""
    if not doc_content:
        return None
    if not any(kw in tx.get("description", "").lower() for kw in LODGING_TERMS):
        return None
    amount = float(tx.get("amount", 0))
    lodging_cap = _cap("lodging_major_metro_usd", LODGING_CAP_MAJOR_METRO_USD)
    if amount <= lodging_cap:
        return None
    is_coastal = any(kw in doc_content.lower() for kw in ["coastal", "point b", "site b"])
    if is_coastal:
        return RuleResult(
            classification=FLAG,
            confidence=0.90,
            issues=[f"Lodging ${amount:.2f}/night exceeds ${lodging_cap:.0f} cap — coastal site"],
            auto_resolution={
                "action": "APPROVE",
                "adjusted_amount": amount,
                "reason": "Coastal site — no alternatives available. Per EX-2025-0828: PL approved.",
                "based_on_prior": "EX-2025-0828",
            },
            analyst_note="Lodging over cap at coastal site — pre-approved per EX-2025-0828.",
            skip_claude=False,
        )
    return RuleResult(
        classification=FLAG,
        confidence=0.90,
        issues=[f"Lodging ${amount:.2f}/night exceeds ${lodging_cap:.0f} major metro cap per contract §4"],
        analyst_note=f"Lodging over cap — obtain PL approval or adjust to ${lodging_cap:.0f}.",
        skip_claude=False,
    )


# ── Ruleset pattern matching rule ────────────────────────────────────────────

_KEYWORD_STOP = {
    "the", "this", "that", "with", "from", "have", "been", "will", "not",
    "and", "but", "for", "per", "are", "was", "has", "had", "its", "over",
    "under", "into", "than", "such", "each", "more", "site", "cost", "rate",
    "date", "item", "type", "code", "name", "amount", "total", "also",
    "when", "then", "does", "did",
    # Generic financial/document terms that appear in nearly every transaction or receipt —
    # keeping them in would cause false positives on every document.
    "expense", "expenses", "receipt", "receipts", "charge", "charges",
    "invoice", "invoices", "payment", "payments", "document", "documents",
    "received", "produced", "applied", "appears", "appearing", "threshold",
    "billed", "billing", "contract", "project", "client", "standard",
}


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful tokens from pattern description or exception type for fuzzy matching."""
    words = re.findall(r'\b[a-z][a-z]{2,}\b', text.lower())
    return [w for w in words if w not in _KEYWORD_STOP]


def rule_prior_exception_patterns(tx: dict, doc_content, _contract, _prior_resolutions) -> Optional[RuleResult]:
    """Match transaction against prior exception patterns extracted into ruleset.json by the Setup phase."""
    rs = _get_ruleset()
    if not rs:
        return None
    patterns = rs.get("prior_exception_patterns", [])
    if not patterns:
        return None

    # Build a single text corpus from the transaction (+ doc when available)
    tx_corpus = " ".join(filter(None, [
        tx.get("description", ""),
        tx.get("note", ""),
        doc_content or "",
    ])).lower()

    for pattern in patterns:
        if not pattern.get("recurring"):
            continue
        # Skip "no receipt / missing document" patterns when we DO have a backup document —
        # those scenarios are already handled deterministically by rule_missing_doc /
        # rule_no_receipt_no_ref before this rule ever runs.
        exc_type = pattern.get("exception_type", "").upper()
        if doc_content and any(tok in exc_type for tok in ("MISSING", "NO_RECEIPT", "RECEIPT")):
            continue
        raw_text = f"{pattern.get('exception_type', '')} {pattern.get('pattern_description', '')}"
        keywords = _extract_keywords(raw_text)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_kw = [k for k in keywords if not (k in seen or seen.add(k))]  # type: ignore[func-returns-value]
        # Need at least 2 distinctive keywords to match reliably; 1-keyword patterns
        # are too generic and produce false positives.
        if len(unique_kw) < 2:
            continue
        matched = [kw for kw in unique_kw if re.search(r'\b' + re.escape(kw) + r'\b', tx_corpus)]
        # Require at least 2 keyword hits regardless of pattern length
        if len(matched) < 2:
            continue

        resolution = pattern.get("resolution", "ESCALATE").upper()
        action = resolution if resolution in ("APPROVE", "REJECT", "ADJUST", "ESCALATE") else "ESCALATE"
        classification = EXEMPT if action == "APPROVE" else FLAG

        return RuleResult(
            classification=classification,
            confidence=0.82,
            issues=[
                f"Matches prior exception pattern {pattern.get('exception_id', '?')}: "
                f"{pattern.get('pattern_description', raw_text)}"
            ],
            auto_resolution={
                "action": action,
                "adjusted_amount": None,
                "reason": pattern.get("reason", "Recurring exception — see prior resolution."),
                "based_on_prior": pattern.get("exception_id"),
            },
            analyst_note=(
                f"Prior pattern match [{pattern.get('exception_id')}] "
                f"keywords: {', '.join(matched)}. Claude verifying."
            ),
            skip_claude=False,  # Always let Claude verify fuzzy pattern matches
        )
    return None


# ── Rule registry ────────────────────────────────────────────────────────────

PRE_DOC_RULES = [
    rule_per_diem,
    rule_under_threshold,
    rule_missing_doc,
    rule_no_receipt_no_ref,
]

POST_DOC_RULES = [
    rule_alcohol,
    rule_personal_items,
    rule_subcontractor_markup,
    rule_foreign_currency,
    rule_meal_cap,
    rule_lodging_cap,
]


def evaluate(tx: dict, doc_content: Optional[str], contract: str, prior_resolutions: list) -> Optional[RuleResult]:
    """
    Run all rules. Returns first RuleResult match or None (escalate to Claude).
    skip_claude=True  → return result directly, no Claude call.
    skip_claude=False → pass result context to Claude, merge findings.

    Order:
      1. PRE_DOC_RULES  — deterministic, no document needed (per diem, missing doc, etc.)
      2. POST_DOC_RULES — require doc_content (alcohol, caps, markup, etc.)
      3. rule_prior_exception_patterns — ruleset-driven fuzzy match; runs last so hardcoded
         rules always take priority, but extracted patterns still catch residual cases.
    """
    for rule in PRE_DOC_RULES:
        result = rule(tx, doc_content, contract, prior_resolutions)
        if result is not None:
            return result
    if doc_content:
        for rule in POST_DOC_RULES:
            result = rule(tx, doc_content, contract, prior_resolutions)
            if result is not None:
                return result
    # Pattern matching runs regardless of doc presence — uses whatever corpus is available
    return rule_prior_exception_patterns(tx, doc_content, contract, prior_resolutions)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_ref(note: str) -> Optional[str]:
    if not note:
        return None
    match = re.search(r'\b(RC-\d+|VI-\d+|ML-\d+)\b', note)
    return match.group(1) if match else None


def _extract_alcohol_lines(doc_content: str) -> list[str]:
    """Extract individual alcohol line items from document text (max 5)."""
    items = []
    for line in doc_content.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if any(term in line_stripped.lower() for term in ALCOHOL_TERMS):
            items.append(line_stripped)
        if len(items) >= 5:
            break
    return items
