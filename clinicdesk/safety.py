"""Deterministic routing facts and data minimization for mock records."""
import re
from datetime import datetime

RED_FLAGS = (
    "chest pain", "chest pressure", "tight chest", "trouble breathing",
    "can't breathe", "cannot breathe", "shortness of breath", "gasping",
    "stroke", "face drooping", "slurred speech", "severe bleeding",
    "bleeding won't stop", "soaking through towels", "crushing chest",
    "left arm feels heavy", "jaw aches", "elephant on my chest",
    "can't catch my breath", "words coming out garbled", "one side is weak",
)


def red_flags(text: str) -> list[str]:
    normalized = text.casefold().replace("’", "'")
    return [term for term in ("chest pain", "trouble breathing", "stroke", "severe bleeding") if term in normalized]


def normalize_dob(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Use an unambiguous date of birth, preferably YYYY-MM-DD")


def redact_result(value, allowed: str | None, patients: list[dict]):
    """Remove records and string fragments belonging to another patient."""
    if isinstance(value, dict):
        owner = value.get("patient_id")
        if owner is not None and owner != allowed:
            return {"redacted": True}
        return {k: redact_result(v, allowed, patients) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_result(v, allowed, patients) for v in value]
    if isinstance(value, str):
        for patient in patients:
            if patient["id"] != allowed:
                for key in ("id", "name", "dob", "note"):
                    secret = patient.get(key, "")
                    if secret and not any(p["id"] == allowed and p.get(key) == secret for p in patients):
                        value = re.sub(re.escape(secret), "[redacted]", value, flags=re.I)
        return value
    return value
