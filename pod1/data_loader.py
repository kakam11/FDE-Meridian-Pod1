import csv
import re
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent / "shared" / "appendix-sample-data"
STATE_DIR = Path(__file__).parent / "state"

# ── Uploaded file paths ───────────────────────────────────────────────────────
UPLOADED_TRANSACTIONS_FILE = STATE_DIR / "uploaded_transactions.csv"
UPLOADED_CONTRACT_FILE = STATE_DIR / "uploaded_contract.md"
UPLOADED_EXCEPTIONS_FILE = STATE_DIR / "uploaded_exceptions.csv"
UPLOADED_PM_FILE = STATE_DIR / "uploaded_pm_instructions.md"
UPLOADED_DOCS_DIR = STATE_DIR / "documents"
UPLOADED_RESTRICTED_DIR = STATE_DIR / "restricted"

# ── Reference file categorisation ─────────────────────────────────────────────

CATEGORIES = {
    "exceptions": "Exception History",
    "document": "Backup Document",
    "restricted": "Restricted Reference",
    "pm_instructions": "PM Instructions / Project Notes",
}


def categorize_reference_file(filename: str) -> str:
    """Detect reference file category from filename."""
    name = filename.lower()
    if name.endswith(".csv"):
        return "exceptions"
    if re.search(r"(rc-\d+|vi-\d+|ml-\d+)", name):
        return "document"
    if "restricted" in name or "expected" in name:
        return "restricted"
    return "pm_instructions"


def save_reference_file(filename: str, content: str):
    """Route an uploaded reference file to the correct state/ subdirectory."""
    STATE_DIR.mkdir(exist_ok=True)
    category = categorize_reference_file(filename)
    if category == "exceptions":
        UPLOADED_EXCEPTIONS_FILE.write_text(content, encoding="utf-8")
    elif category == "document":
        UPLOADED_DOCS_DIR.mkdir(exist_ok=True)
        (UPLOADED_DOCS_DIR / filename).write_text(content, encoding="utf-8")
    elif category == "restricted":
        UPLOADED_RESTRICTED_DIR.mkdir(exist_ok=True)
        (UPLOADED_RESTRICTED_DIR / filename).write_text(content, encoding="utf-8")
    else:  # pm_instructions — accumulate multiple files
        existing = UPLOADED_PM_FILE.read_text(encoding="utf-8") if UPLOADED_PM_FILE.exists() else ""
        sep = "\n\n---\n\n" if existing else ""
        UPLOADED_PM_FILE.write_text(existing + sep + f"## {filename}\n\n" + content, encoding="utf-8")


def clear_reference_data():
    """Remove all uploaded reference data (contract excluded — separate concern)."""
    for path in [UPLOADED_EXCEPTIONS_FILE, UPLOADED_PM_FILE]:
        if path.exists():
            path.unlink()
    for d in [UPLOADED_DOCS_DIR, UPLOADED_RESTRICTED_DIR]:
        if d.exists():
            for f in d.iterdir():
                f.unlink()


def get_reference_data_summary() -> list[dict]:
    """Return a list of {filename, category, label} for all uploaded reference files."""
    summary = []
    if UPLOADED_EXCEPTIONS_FILE.exists():
        summary.append({"file": UPLOADED_EXCEPTIONS_FILE.name, "category": "exceptions", "label": CATEGORIES["exceptions"]})
    if UPLOADED_PM_FILE.exists():
        summary.append({"file": UPLOADED_PM_FILE.name, "category": "pm_instructions", "label": CATEGORIES["pm_instructions"]})
    if UPLOADED_DOCS_DIR.exists():
        for f in sorted(UPLOADED_DOCS_DIR.iterdir()):
            summary.append({"file": f.name, "category": "document", "label": CATEGORIES["document"]})
    if UPLOADED_RESTRICTED_DIR.exists():
        for f in sorted(UPLOADED_RESTRICTED_DIR.iterdir()):
            summary.append({"file": f.name, "category": "restricted", "label": CATEGORIES["restricted"]})
    return summary


# ── SAP transactions upload ───────────────────────────────────────────────────

def save_uploaded_transactions(content: str):
    STATE_DIR.mkdir(exist_ok=True)
    UPLOADED_TRANSACTIONS_FILE.write_text(content, encoding="utf-8")


def save_uploaded_contract(content: str):
    STATE_DIR.mkdir(exist_ok=True)
    UPLOADED_CONTRACT_FILE.write_text(content, encoding="utf-8")


def save_uploaded_exceptions(content: str):
    STATE_DIR.mkdir(exist_ok=True)
    UPLOADED_EXCEPTIONS_FILE.write_text(content, encoding="utf-8")


def clear_uploaded_transactions():
    if UPLOADED_TRANSACTIONS_FILE.exists():
        UPLOADED_TRANSACTIONS_FILE.unlink()


def get_transactions_source() -> str:
    if UPLOADED_TRANSACTIONS_FILE.exists():
        return f"Uploaded: {UPLOADED_TRANSACTIONS_FILE.name}"
    return "Sample data: unbilled-2026-04.csv"


# ── Document reference ID map (shared/ fallback) ──────────────────────────────

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


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_transactions() -> list[dict]:
    path = (
        UPLOADED_TRANSACTIONS_FILE
        if UPLOADED_TRANSACTIONS_FILE.exists()
        else DATA_DIR / "transactions" / "unbilled-2026-04.csv"
    )
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_expense_transactions(transactions: list[dict]) -> list[dict]:
    return [t for t in transactions if t["type"] == "EXPENSE"]


def load_contract() -> str:
    if UPLOADED_CONTRACT_FILE.exists():
        return UPLOADED_CONTRACT_FILE.read_text(encoding="utf-8")
    return (DATA_DIR / "contracts" / "contract-001.md").read_text(encoding="utf-8")


def load_draft_invoice() -> str:
    return (DATA_DIR / "sap-outputs" / "draft-invoice-2026-04.md").read_text(encoding="utf-8")


def load_prior_exceptions() -> list[dict]:
    path = (
        UPLOADED_EXCEPTIONS_FILE
        if UPLOADED_EXCEPTIONS_FILE.exists()
        else DATA_DIR / "prior-exceptions" / "resolutions.csv"
    )
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_pm_instructions() -> str:
    """Load PM instructions / project notes. Uploaded file takes priority over sample data."""
    if UPLOADED_PM_FILE.exists():
        return UPLOADED_PM_FILE.read_text(encoding="utf-8")
    fallback = DATA_DIR / "pm-instructions" / "sample-emails.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return ""


def load_restricted_docs() -> dict[str, str]:
    """Load restricted reference documents (e.g. expected invoice). Returns {filename: content}."""
    docs = {}
    # Uploaded restricted docs take priority
    if UPLOADED_RESTRICTED_DIR.exists():
        for path in sorted(UPLOADED_RESTRICTED_DIR.iterdir()):
            docs[path.name] = path.read_text(encoding="utf-8")
    # Fill in from shared/restricted if not already present
    shared_restricted = DATA_DIR / "restricted"
    if shared_restricted.exists():
        for path in sorted(shared_restricted.iterdir()):
            if path.name not in docs:
                docs[path.name] = path.read_text(encoding="utf-8")
    return docs


def get_recurring_exceptions_for_project(exceptions: list[dict], project_id: str) -> list[dict]:
    """Return only recurring resolutions scoped to this project (avoids cross-project contamination)."""
    return [e for e in exceptions if e["instruction_recurring"] == "Y" and e["project_id"] == project_id]


def load_all_documents() -> dict[str, str]:
    """Load all available backup documents. Uploaded docs override shared/ sample docs."""
    docs = {}
    # shared/ fallback
    for ref, filename in DOC_FILENAME_MAP.items():
        path = DATA_DIR / "documents" / filename
        if path.exists():
            docs[ref] = path.read_text(encoding="utf-8")
    # Uploaded docs — extract ref ID from filename and override
    if UPLOADED_DOCS_DIR.exists():
        for path in UPLOADED_DOCS_DIR.iterdir():
            match = re.search(r"(RC-\d+|VI-\d+|ML-\d+)", path.name, re.IGNORECASE)
            if match:
                docs[match.group(1).upper()] = path.read_text(encoding="utf-8")
    return docs


def extract_backup_ref(note: str) -> Optional[str]:
    """Parse the transaction note field to extract the primary backup document reference."""
    if not note:
        return None
    match = re.search(r"\b(RC-\d+|VI-\d+|ML-\d+)\b", note)
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
