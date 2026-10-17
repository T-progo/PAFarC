"""Suggested test/kit (Fabricante, Lote, Validade) in the add-exam form. Plain functions, no
Streamlit, so the rule can be tested on its own."""

from datetime import date

EMPTY_KIT = {"manufacturer": "", "lot": "", "expiry": None}


def kit_from_api(kit: dict) -> dict:
    """The API suggestion as form values (empty when there is none)."""
    return {
        "manufacturer": kit.get("manufacturer") or "",
        "lot": kit.get("lot") or "",
        "expiry": date.fromisoformat(kit["expiry_date"]) if kit.get("expiry_date") else None,
    }


def merge_kit_suggestion(current: dict, previous: dict, new: dict) -> dict:
    """Form values after a new suggestion: a field takes it only while it is empty or still
    holds the previous suggestion, so values the pharmacist typed are never overwritten."""
    return {
        field: new[field] if current.get(field) in ("", None) or current.get(field) == previous.get(field)
        else current[field]
        for field in EMPTY_KIT
    }
