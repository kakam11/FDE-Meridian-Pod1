"""
One-time ruleset builder.

Upload a contract (text) and prior exceptions (CSV text) → Claude extracts
structured rules → saved to state/ruleset.json.

rules.py reads this file on every analysis run.
Re-building replaces the existing ruleset.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

STATE_DIR = Path(__file__).parent / "state"
RULESET_FILE = STATE_DIR / "ruleset.json"

_SYSTEM = (
    "You are a billing compliance analyst. Extract structured billing rules from a "
    "Master Services Agreement and prior exception history. "
    "Output ONLY valid JSON — no prose, no markdown fences."
)

_PROMPT = """\
## Master Services Agreement
{contract}

## Prior Exception Resolutions (CSV rows)
{exceptions}

## PM Instructions & Project Notes
{pm_instructions}

## Restricted Reference Documents
{restricted_docs}

Extract billing rules into this exact JSON structure. Use exact numbers from the contract.
For alcohol_terms and personal_terms be comprehensive with common synonyms.
Include ONLY rows where instruction_recurring=Y in prior_exception_patterns.
Incorporate any additional rules or constraints found in PM instructions.

{{
  "contract_id": "extracted from contract header",
  "project_id": "extracted from contract header",
  "caps": {{
    "meal_per_day_usd": 90.0,
    "lodging_major_metro_usd": 275.0,
    "lodging_elsewhere_usd": 195.0,
    "per_diem_usd": 65.0,
    "receipt_threshold_usd": 25.0,
    "subcontractor_markup_pct": 0.08
  }},
  "not_reimbursable": ["alcohol", "personal items", "entertainment without PL approval"],
  "alcohol_terms": ["wine", "beer", "spirits", "ale", "cocktail", "lager", "whisky",
                    "whiskey", "gin", "vodka", "rum", "champagne", "prosecco", "cider",
                    "liquor", "house red", "house white"],
  "personal_terms": ["laundry", "dry cleaning", "personal item", "toiletries",
                     "clothing", "haircut", "spa", "gym"],
  "exempt_no_receipt": ["per diem", "mileage"],
  "prior_exception_patterns": [
    {{
      "exception_id": "EX-...",
      "exception_type": "...",
      "pattern_description": "...",
      "resolution": "APPROVE|REJECT|ADJUST",
      "reason": "...",
      "recurring": true
    }}
  ],
  "pm_rules": ["any rules extracted from PM instructions, e.g. 'PRIN time capped at 5%'"],
  "contract_notes": ["note1", "note2"]
}}"""


def build_ruleset(
    contract_text: str,
    exceptions_csv_text: str,
    pm_instructions_text: str = "",
    restricted_docs_text: str = "",
) -> dict:
    """Call Claude to extract a structured ruleset. Saves to state/ruleset.json."""
    STATE_DIR.mkdir(exist_ok=True)
    client = anthropic.Anthropic()

    prompt = _PROMPT.format(
        contract=contract_text[:6000],
        exceptions=exceptions_csv_text[:3000],
        pm_instructions=pm_instructions_text[:3000] if pm_instructions_text else "None provided.",
        restricted_docs=restricted_docs_text[:2000] if restricted_docs_text else "None provided.",
    )

    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()

    raw = ""
    for block in reversed(msg.content):
        if hasattr(block, "text"):
            raw = block.text
            break

    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    try:
        ruleset = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start >= 0 and end > start:
            ruleset = json.loads(raw[start:end])
        else:
            raise ValueError(f"Claude returned non-JSON: {raw[:300]}")

    ruleset["built_at"] = datetime.now(timezone.utc).isoformat()
    RULESET_FILE.write_text(json.dumps(ruleset, indent=2))
    return ruleset


def load_ruleset() -> Optional[dict]:
    """Return the persisted ruleset or None if not yet built."""
    if RULESET_FILE.exists():
        return json.loads(RULESET_FILE.read_text())
    return None


def clear_ruleset():
    """Remove the persisted ruleset (force rebuild on next setup)."""
    if RULESET_FILE.exists():
        RULESET_FILE.unlink()
