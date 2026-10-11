"""Single-process local JSON persistence with atomic replacement."""
import copy
import json
import os
from pathlib import Path

class Store:
    def __init__(self, path: Path, initial: dict | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if initial is not None:
            self.data = copy.deepcopy(initial)
            for key in ("patients", "clinicians", "availability", "appointments", "escalations", "messages"):
                self.data.setdefault(key, [])
            self.save()
        else:
            self.data = json.loads(self.path.read_text())

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n")
        os.replace(temporary, self.path)

    def snapshot(self) -> dict:
        """Return an independent copy suitable for transaction rollback."""
        return copy.deepcopy(self.data)


def export_filename(patient_id: str, extension: str = "json") -> str:
    """Name an optional manual export without allowing path traversal."""
    safe = "".join(c for c in patient_id if c.isalnum() or c in "-_")
    if not safe or extension not in ("json", "csv"):
        raise ValueError("Invalid export name")
    return f"{safe}.{extension}"
