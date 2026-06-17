import csv
import re
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent / "shared" / "appendix-sample-data"

# Maps doc reference IDs to actual filenames in the documents/ folder
DOC_FILENAME_MAP = {
    "RC-001": "RC-001-flight-outbound.md",
    "RC-002": "RC-002-flight-return.md",
    "RC-003": "RC-003-hotel-folio.md",
    "RC-007": "RC-007-team-dinner.md",
    "RC-012": "RC-012-hotel-overcap.md",
    "RC-013": "RC-013-client-dinner.md",
    "RC-014": "RC-014-airport-lounge.md",
    "RC-015": "RC-015-foreign-currency.md",
    "RC-016": "RC-016-composite.md",
    "RC-017": "RC-017-personal-laundry.md",
    "RC-018": "RC-018-unreadable.md",
    "RC-019": "RC-019-mismatched.md",
    "ML-001": "ML-001-mileage-log.md",
    "VI-001": "VI-001-workshop-vendor.md",
    "VI-002": "VI-002-subcontractor.md",
}


def load_transactions() -> list[dict]:
    path = DATA_DIR / "transactions" / "unbilled-2026-04.csv"
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_expense_transactions(transactions: list[dict]) -> list[dict]:
    return [t for t in transactions if t["type"] == "EXPENSE"]


def load_contract() -> str:
    return (DATA_DIR / "contracts" / "contract-001.md").read_text(encoding="utf-8")


def load_draft_invoice() -> str:
    return (DATA_DIR / "sap-outputs" / "draft-invoice-2026-04.md").read_text(encoding="utf-8")


def load_prior_exceptions() -> list[dict]:
    path = DATA_DIR / "prior-exceptions" / "resolutions.csv"
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_recurring_exceptions_for_project(exceptions: list[dict], project_id: str) -> list[dict]:
    """Return only recurring resolutions scoped to this project (avoids cross-project contamination)."""
    return [e for e in exceptions if e["instruction_recurring"] == "Y" and e["project_id"] == project_id]


def load_all_documents() -> dict[str, str]:
    """Load all available documents. Returns {doc_ref: content}."""
    docs = {}
    for ref, filename in DOC_FILENAME_MAP.items():
        path = DATA_DIR / "documents" / filename
        if path.exists():
            docs[ref] = path.read_text(encoding="utf-8")
    return docs


def extract_backup_ref(note: str) -> Optional[str]:
    """Parse the transaction note field to extract the primary backup document reference."""
    if not note:
        return None
    match = re.search(r'\b(RC-\d+|VI-\d+|ML-\d+)\b', note)
    return match.group(1) if match else None


def format_transaction(tx: dict) -> str:
    return (
        f"Transaction ID: {tx['transaction_id']}\n"
        f"Date: {tx['transaction_date']}\n"
        f"Description: {tx['description']}\n"
        f"Amount: {tx['amount']} {tx['currency']}\n"
        f"Employee: {tx['employee_id']}\n"
        f"Note: {tx['note']}\n"
        f"Hold: {tx['hold_flag']} {tx.get('hold_reason', '')}"
    )


def format_prior_resolutions(resolutions: list[dict]) -> str:
    if not resolutions:
        return "None on file."
    return "\n".join(
        f"- [{r['exception_id']}] {r['exception_type']}: {r['resolution']}"
        for r in resolutions
    )
