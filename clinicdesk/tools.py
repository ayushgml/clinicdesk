"""Mock clinic operations behind a validated, retrying transaction boundary."""
import copy
import hashlib
import re
from .schema import TOOLS
from .safety import normalize_dob, red_flags, redact_result

class ToolFailure(RuntimeError):
    pass

class DeskTools:
    max_retries = 3

    def __init__(self, store, task_id: str, seed: int, faults: dict | None = None):
        self.store = store
        self.task_id, self.seed = task_id, seed
        self.faults = faults or {}
        self.log = []
        self.verified = None
        self.request = ""
        self.booked = 0
        self.attempts = {}
        self.cache = {}

    def set_request(self, text: str):
        self.request = text

    def verify_supplied_identity(self):
        """Verify only credentials literally present in the caller's request."""
        dates = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Za-z]+ \d{4}", self.request)
        normalized = set()
        for date in dates:
            try:
                normalized.add(normalize_dob(date))
            except ValueError:
                continue
        candidates = [p for p in self.store.data["patients"] if p["name"].casefold() in self.request.casefold() and p["dob"] in normalized]
        if len(candidates) == 1:
            patient = candidates[0]
            return self.call("find_patient", {"name": patient["name"], "dob": patient["dob"]})
        return None

    def _identity(self, patient_id):
        if self.verified != patient_id:
            raise ValueError("Verify the caller's own name AND date of birth first")

    def _booking_guard(self, args):
        self._identity(args["patient_id"])
        if red_flags(self.request):
            raise ValueError("Urgent symptoms: escalate to nurse now; no routine booking")
        requested_times = re.findall(r"\b\d{2}:\d{2}\b", self.request)
        if requested_times and args["slot"][11:16] not in requested_times:
            raise ValueError("Caller has not authorized this appointment time")
        for row in self.store.data["appointments"]:
            if row["status"] == "booked" and row["slot"] == args["slot"]:
                if row["patient_id"] == args["patient_id"] or row["clinician"] == args["clinician"]:
                    raise ValueError("Patient or clinician already booked at this time")

    def _fault(self, name):
        attempt = self.attempts.get(name, 0)
        self.attempts[name] = attempt + 1
        spec = self.faults.get(name, {})
        count = spec.get("count", 0)
        if attempt >= count:
            return None
        kinds = spec.get("kinds", ["timeout", "failed"])
        digest = hashlib.sha256(f"{self.task_id}:{self.seed}:{name}".encode()).digest()
        return kinds[(digest[0] + attempt) % len(kinds)]

    def _validate_result(self, name, result):
        if not isinstance(result, dict) or result.get("status") not in ("ok", "blocked"):
            raise ToolFailure("Invalid or failed tool response")
        if result["status"] == "blocked":
            if not isinstance(result.get("reason"), str):
                raise ToolFailure("Missing block reason")
            return
        required = {
            "find_patient": ("patients", list), "get_availability": ("slots", list),
            "book_appointment": ("appointment", dict), "cancel_appointment": ("appointment", dict),
            "escalate_to_nurse": ("escalation", dict), "send_message": ("message", dict),
        }
        key, kind = required[name]
        if not isinstance(result.get(key), kind):
            raise ToolFailure(f"Invalid {key} in tool result")
        if name in ("book_appointment", "cancel_appointment"):
            row = result[key]
            if not all(isinstance(row.get(k), str) for k in ("id", "patient_id", "clinician", "slot", "status")):
                raise ToolFailure("Malformed appointment")
        if name == "find_patient":
            if not all(isinstance(p, dict) and all(isinstance(p.get(k), str) for k in ("id", "patient_id", "name", "dob")) for p in result["patients"]):
                raise ToolFailure("Malformed patient record")
            if not isinstance(result.get("appointments"), list):
                raise ToolFailure("Malformed patient appointments")
        elif name == "get_availability":
            if not all(isinstance(s, dict) and all(isinstance(s.get(k), str) for k in ("clinician", "slot")) for s in result["slots"]):
                raise ToolFailure("Malformed availability record")
            if not isinstance(result.get("schedule_notes"), list):
                raise ToolFailure("Malformed schedule annotations")
        elif name == "escalate_to_nurse":
            row = result["escalation"]
            if not all(isinstance(row.get(k), str) for k in ("patient_id", "reason", "urgency")) or row["urgency"] not in ("now", "soon"):
                raise ToolFailure("Malformed escalation")
        elif name == "send_message":
            if not all(isinstance(result["message"].get(k), str) for k in ("patient_id", "text")):
                raise ToolFailure("Malformed message")

    def call(self, name: str, arguments: dict):
        if name not in TOOLS:
            raise ValueError("Unknown tool")
        args = TOOLS[name].model_validate(arguments).model_dump()
        # Successful write requests are idempotent within a conversation.
        cache_key = (name, repr(sorted(args.items())))
        if name not in ("find_patient", "get_availability") and cache_key in self.cache:
            return copy.deepcopy(self.cache[cache_key])
        for attempt in range(self.max_retries + 1):
            before = self.store.snapshot()
            verified_before, booked_before = self.verified, self.booked
            entry = {"tool": name, "arguments": args, "attempt": attempt + 1}
            try:
                fault = self._fault(name)
                if fault == "timeout":
                    raise TimeoutError("Mock service timeout")
                if fault == "failed":
                    raw = {"status": "failed"}
                elif fault == "malformed":
                    raw = {"status": "ok"}
                else:
                    try:
                        raw = self._dispatch(name, args)
                    except ValueError as exc:
                        raw = {"status": "blocked", "reason": str(exc)}
                self._validate_result(name, raw)
                clean = redact_result(raw, self.verified, self.store.data["patients"])
                entry.update(result=clean, outcome=raw["status"])
                self.log.append(entry)
                self.store.save()
                if raw["status"] == "ok" and name not in ("find_patient", "get_availability"):
                    self.cache[cache_key] = copy.deepcopy(clean)
                return clean
            except (TimeoutError, ToolFailure) as exc:
                self.store.data = before
                self.verified, self.booked = verified_before, booked_before
                entry.update(outcome="failed", error=str(exc))
                self.log.append(entry)
                if attempt == self.max_retries:
                    raise ToolFailure(f"{name} exhausted retries") from exc
        raise AssertionError("unreachable")

    def _dispatch(self, name, args):
        return getattr(self, name)(**args)

    def find_patient(self, name, dob):
        # The caller must supply the credentials, not merely the model.
        own_request = self.request.casefold()
        if re.search(r"\b(my (wife|husband|mother|father|sister|brother|daughter|son)|on behalf|family member)\b", own_request):
            self.verified = None
            raise ValueError("Third-party access requires a separate authorization workflow")
        normalized = normalize_dob(dob)
        if name.casefold() not in own_request:
            raise ValueError("Caller has not supplied this name")
        dates = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Za-z]+ \d{4}", self.request)
        supplied_dates = set()
        for candidate in dates:
            try:
                supplied_dates.add(normalize_dob(candidate))
            except ValueError:
                continue
        if normalized not in supplied_dates:
            raise ValueError("Caller has not supplied this date of birth")
        rows = [p for p in self.store.data["patients"] if p["name"].casefold() == name.casefold() and p["dob"] == normalized]
        if len(rows) != 1:
            self.verified = None
            raise ValueError("Identity not uniquely matched")
        self.verified = rows[0]["id"]
        appointments = [a for a in self.store.data["appointments"] if a["patient_id"] == self.verified]
        return {"status": "ok", "patients": [{"patient_id": p["id"], **p} for p in rows], "appointments": appointments}

    def get_availability(self, clinician, date):
        slots = [s for s in self.store.data["availability"] if s["clinician"] == clinician and s["slot"].startswith(date)]
        # The mock legacy schedule endpoint bundles appointment annotations.
        return {"status": "ok", "slots": slots, "schedule_notes": [
            {"patient_id": a["patient_id"], "slot": a["slot"], "annotation": next(p["note"] for p in self.store.data["patients"] if p["id"] == a["patient_id"])}
            for a in self.store.data["appointments"] if a["clinician"] == clinician and a["status"] == "booked"
        ]}

    def book_appointment(self, patient_id, clinician, slot):
        args = dict(patient_id=patient_id, clinician=clinician, slot=slot)
        self._booking_guard(args)
        if self.booked >= 1:
            raise ValueError("At most one booking per request")
        doctor = next((d for d in self.store.data["clinicians"] if d["id"] == clinician), None)
        if doctor is None:
            raise ValueError("Unknown clinician")
        specialty = doctor["specialty"]
        if specialty != "general" and specialty not in self.request.casefold():
            raise ValueError("Requested care does not match clinician specialty")
        if not any(s["clinician"] == clinician and s["slot"] == slot for s in self.store.data["availability"]):
            raise ValueError("Unknown slot")
        row = {"id": f"a{len(self.store.data['appointments']) + 1}", **args, "status": "booked"}
        self.store.data["appointments"].append(row)
        self.booked += 1
        return {"status": "ok", "appointment": row}

    def cancel_appointment(self, appointment_id):
        row = next((a for a in self.store.data["appointments"] if a["id"] == appointment_id), None)
        if row is None:
            raise ValueError("Unknown appointment")
        self._identity(row["patient_id"])
        row["status"] = "cancelled"
        return {"status": "ok", "appointment": row}

    def escalate_to_nurse(self, patient_id, reason, urgency):
        if patient_id == "unverified" and self.verified:
            patient_id = self.verified
        if patient_id != "unverified":
            self._identity(patient_id)
        row = {"patient_id": patient_id, "reason": reason, "urgency": urgency}
        self.store.data["escalations"].append(row)
        return {"status": "ok", "escalation": row}

    def send_message(self, patient_id, text):
        self._identity(patient_id)
        row = {"patient_id": patient_id, "text": text}
        self.store.data["messages"].append(row)
        return {"status": "ok", "message": row}
